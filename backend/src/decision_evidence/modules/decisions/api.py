from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from decision_evidence.config import get_settings
from decision_evidence.db import models as m
from decision_evidence.errors import ApiError, conflict, not_found
from decision_evidence.identity import repository as identity
from decision_evidence.jobs.service import enqueue_job
from decision_evidence.modules.analysis.api import JobOut, job_out
from decision_evidence.modules.analysis.service import check_budget, provider_label
from decision_evidence.modules.common import Page, Paging, check_version, etag, require_if_match, tenant_settings
from decision_evidence.modules.decisions import export, service
from decision_evidence.tenancy.context import TenantContext, admin, editor, viewer

router = APIRouter(prefix="/api/v1/workspaces/{tenant_id}", tags=["decisions"])
DState = Literal["draft", "in_review", "approved", "superseded"]


class CommentOut(BaseModel):
    id: uuid.UUID
    author_user_id: uuid.UUID
    author_name: str | None
    body: str
    created_at: datetime


class DecisionListItem(BaseModel):
    id: uuid.UUID
    initiative_id: uuid.UUID
    initiative_title: str
    revision: int
    title: str
    state: str
    approved_at: datetime | None
    updated_at: datetime


class DecisionOut(BaseModel):
    id: uuid.UUID
    initiative_id: uuid.UUID
    initiative_title: str
    problem_id: uuid.UUID
    problem_title: str
    revision: int
    title: str
    state: str
    options: list[dict[str, Any]]
    recommendation_text: str
    evidence_snapshot: dict[str, Any]
    scoring_snapshot: dict[str, Any]
    snapshot_taken_at: datetime | None
    scoring_policy_id: uuid.UUID | None
    ai_draft: dict[str, Any] | None
    ai_run: dict[str, Any] | None
    created_by_name: str | None
    submitted_by_name: str | None
    approved_by_name: str | None
    approved_at: datetime | None
    version: int
    updated_at: datetime
    comments: list[CommentOut]
    revisions: list[dict[str, Any]]
    editable: bool
    drift: dict[str, Any] | None
    can_approve: bool
    approve_block_reason: str | None


def build(ctx: TenantContext, s: Any, doc_id: uuid.UUID, with_drift: bool = True) -> DecisionOut:
    d = service.get_doc(s, doc_id)
    ini = s.get(m.Initiative, d.initiative_id)
    prob = s.get(m.Problem, ini.problem_id)
    user_ids = {u for u in (d.created_by, d.submitted_by, d.approved_by) if u}
    comments = list(s.scalars(select(m.DecisionComment).where(m.DecisionComment.decision_document_id == d.id).order_by(m.DecisionComment.created_at)))
    user_ids |= {c.author_user_id for c in comments}
    names = {k: (v["display_name"] or v["email"]) for k, v in identity.user_names(ctx.tenant_id, user_ids).items()}
    revisions = [{"id": str(r.id), "revision": r.revision, "state": r.state, "approved_at": r.approved_at.isoformat() if r.approved_at else None}
                 for r in s.scalars(select(m.DecisionDocument).where(m.DecisionDocument.initiative_id == d.initiative_id).order_by(m.DecisionDocument.revision.desc()))]
    run = s.get(m.AiRun, d.ai_run_id) if d.ai_run_id else None
    ts = tenant_settings(s)
    block = None
    if d.state != "in_review":
        block = "Nur Vorlagen in Prüfung können freigegeben werden."
    elif not ctx.has_role("admin"):
        block = "Für die Freigabe ist die Rolle Admin oder Owner nötig."
    elif not ts.allow_self_approval and ctx.user_id in (d.created_by, d.submitted_by):
        block = "Vier-Augen-Prinzip: Autorin oder Autor dürfen nicht selbst freigeben."
    return DecisionOut(
        id=d.id, initiative_id=d.initiative_id, initiative_title=ini.title, problem_id=prob.id, problem_title=prob.title, revision=d.revision, title=d.title, state=d.state,
        options=d.options, recommendation_text=d.recommendation_text, evidence_snapshot=d.evidence_snapshot, scoring_snapshot=d.scoring_snapshot,
        snapshot_taken_at=d.snapshot_taken_at, scoring_policy_id=d.scoring_policy_id, ai_draft=d.ai_draft,
        ai_run={"provider": run.provider, "model": run.model, "demo": run.provider == "mock", "created_at": run.created_at.isoformat()} if run else None,
        created_by_name=names.get(d.created_by) if d.created_by else None, submitted_by_name=names.get(d.submitted_by) if d.submitted_by else None,
        approved_by_name=names.get(d.approved_by) if d.approved_by else None, approved_at=d.approved_at, version=d.version, updated_at=d.updated_at,
        comments=[CommentOut(id=c.id, author_user_id=c.author_user_id, author_name=names.get(c.author_user_id), body=c.body, created_at=c.created_at) for c in comments],
        revisions=revisions, editable=d.state == "draft", drift=service.drift(s, d) if with_drift else None, can_approve=block is None, approve_block_reason=block)


@router.get("/decisions", response_model=Page[DecisionListItem])
def list_decisions(ctx: Annotated[TenantContext, viewer], paging: Annotated[Paging, Depends()], state: DState | None = None,
                   initiative_id: uuid.UUID | None = None) -> Page[DecisionListItem]:
    with ctx.session() as s:
        cond = []
        if state:
            cond.append(m.DecisionDocument.state == state)
        if initiative_id:
            cond.append(m.DecisionDocument.initiative_id == initiative_id)
        total = s.scalar(select(func.count()).select_from(m.DecisionDocument).where(*cond)) or 0
        rows = s.execute(select(m.DecisionDocument, m.Initiative.title).join(m.Initiative, m.Initiative.id == m.DecisionDocument.initiative_id).where(*cond)
                         .order_by(m.DecisionDocument.updated_at.desc(), m.DecisionDocument.id).limit(paging.limit).offset(paging.offset)).all()
        return Page[DecisionListItem](items=[DecisionListItem(id=d.id, initiative_id=d.initiative_id, initiative_title=t, revision=d.revision, title=d.title, state=d.state,
                                                              approved_at=d.approved_at, updated_at=d.updated_at) for d, t in rows],
                                      total=total, limit=paging.limit, offset=paging.offset)


@router.post("/initiatives/{initiative_id}/decisions", response_model=DecisionOut, status_code=201)
def create_decision(ctx: Annotated[TenantContext, editor], initiative_id: uuid.UUID) -> DecisionOut:
    with ctx.session() as s:
        doc = service.create_revision(s, initiative_id, ctx.user_id)
        ctx.audit(s, "decision.revision_created", "decision_document", doc.id, revision=doc.revision)
        return build(ctx, s, doc.id)


@router.get("/decisions/{decision_id}", response_model=DecisionOut)
def get_decision(ctx: Annotated[TenantContext, viewer], decision_id: uuid.UUID, response: Response) -> DecisionOut:
    with ctx.session() as s:
        out = build(ctx, s, decision_id)
    response.headers["ETag"] = etag(out.version)
    return out


class DecisionPatch(BaseModel):
    title: str | None = Field(default=None, max_length=300)
    recommendation_text: str | None = Field(default=None, max_length=20000)
    options: list[dict[str, Any]] | None = None
    scoring_policy_id: uuid.UUID | None = None


@router.patch("/decisions/{decision_id}", response_model=DecisionOut)
def patch_decision(ctx: Annotated[TenantContext, editor], decision_id: uuid.UUID, body: DecisionPatch, expected: Annotated[int, Depends(require_if_match)]) -> DecisionOut:
    with ctx.session() as s:
        d = service.get_doc(s, decision_id, lock=True)
        if d.state != "draft":
            raise conflict("immutable", "Freigegebene oder eingereichte Revisionen sind unveränderlich", "Für neue Erkenntnisse bitte eine neue Revision anlegen.")
        check_version(d.version, expected)
        if body.title is not None:
            d.title = body.title
        if body.recommendation_text is not None:
            d.recommendation_text = body.recommendation_text
        if body.options is not None:
            d.options = service.normalise_options(body.options)
        if body.scoring_policy_id is not None:
            from decision_evidence.modules.scoring.service import get_policy
            d.scoring_policy_id = get_policy(s, body.scoring_policy_id).id
        d.version += 1
        s.flush()
        ctx.audit(s, "decision.edited", "decision_document", d.id)
        return build(ctx, s, d.id)


@router.post("/decisions/{decision_id}/snapshot", response_model=DecisionOut)
def refresh_snapshot(ctx: Annotated[TenantContext, editor], decision_id: uuid.UUID) -> DecisionOut:
    with ctx.session() as s:
        d = service.get_doc(s, decision_id, lock=True)
        service.refresh_snapshot(s, d)
        ctx.audit(s, "decision.snapshot_refreshed", "decision_document", d.id)
        return build(ctx, s, d.id)


@router.post("/decisions/{decision_id}/submit", response_model=DecisionOut)
def submit(ctx: Annotated[TenantContext, editor], decision_id: uuid.UUID) -> DecisionOut:
    with ctx.session() as s:
        d = service.get_doc(s, decision_id, lock=True)
        service.submit(s, d, ctx.user_id)
        ctx.audit(s, "decision.submitted", "decision_document", d.id, revision=d.revision)
        return build(ctx, s, d.id)


@router.post("/decisions/{decision_id}/request-changes", response_model=DecisionOut)
def request_changes(ctx: Annotated[TenantContext, admin], decision_id: uuid.UUID) -> DecisionOut:
    with ctx.session() as s:
        d = service.get_doc(s, decision_id, lock=True)
        service.request_changes(d)
        ctx.audit(s, "decision.changes_requested", "decision_document", d.id)
        return build(ctx, s, d.id)


@router.post("/decisions/{decision_id}/approve", response_model=DecisionOut)
def approve(ctx: Annotated[TenantContext, admin], decision_id: uuid.UUID) -> DecisionOut:
    with ctx.session() as s:
        d = service.get_doc(s, decision_id, lock=True)
        service.approve(s, d, ctx.user_id, tenant_settings(s).allow_self_approval)
        ctx.audit(s, "decision.approved", "decision_document", d.id, revision=d.revision, initiative_id=str(d.initiative_id))
        return build(ctx, s, d.id)


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


@router.post("/decisions/{decision_id}/comments", response_model=DecisionOut, status_code=201)
def add_comment(ctx: Annotated[TenantContext, editor], decision_id: uuid.UUID, body: CommentIn) -> DecisionOut:
    with ctx.session() as s:
        d = service.get_doc(s, decision_id)
        s.add(m.DecisionComment(decision_document_id=d.id, author_user_id=ctx.user_id, body=body.body.strip()))
        s.flush()
        ctx.audit(s, "decision.commented", "decision_document", d.id)
        return build(ctx, s, d.id)


class RationaleAccepted(BaseModel):
    job: JobOut
    created: bool
    provider: dict[str, Any]


@router.post("/decisions/{decision_id}/ai-draft", response_model=RationaleAccepted, status_code=202)
def request_ai_draft(ctx: Annotated[TenantContext, editor], decision_id: uuid.UUID) -> RationaleAccepted:
    settings = get_settings()
    with ctx.session() as s:
        d = service.get_doc(s, decision_id)
        if d.state != "draft":
            raise conflict("not_draft", "KI-Entwürfe sind nur für Entwürfe möglich")
        if d.snapshot_taken_at is None:
            raise ApiError(422, "snapshot_missing", "Bitte zuerst den Belegstand einfrieren", "Der KI-Entwurf arbeitet ausschließlich mit dem Snapshot.")
        ts = tenant_settings(s)
        check_budget(s, ts.ai_monthly_call_budget)
        key = f"rationale:{d.id}:{d.version}:{settings.ai_provider}"
        job, created = enqueue_job(s, kind="draft_rationale", idempotency_key=key, payload={"document_id": str(d.id), "version": d.version},
                                   requested_by=ctx.user_id, max_running=ts.max_running_jobs)
        if created:
            ctx.audit(s, "decision.ai_draft_requested", "decision_document", d.id, provider=settings.ai_provider)
        return RationaleAccepted(job=job_out(ctx, job), created=created, provider=provider_label(settings))


def _comments_for_export(ctx: TenantContext, s: Any, doc: m.DecisionDocument) -> tuple[dict[uuid.UUID, str], list[dict[str, Any]]]:
    comments = list(s.scalars(select(m.DecisionComment).where(m.DecisionComment.decision_document_id == doc.id).order_by(m.DecisionComment.created_at)))
    ids = {c.author_user_id for c in comments} | {u for u in (doc.approved_by, doc.created_by) if u}
    names = {k: (v["display_name"] or v["email"] or "unbekannt") for k, v in identity.user_names(ctx.tenant_id, ids).items()}
    return names, [{"author": names.get(c.author_user_id, "unbekannt"), "created_at": c.created_at.isoformat(timespec="minutes"), "body": c.body} for c in comments]


@router.get("/decisions/{decision_id}/export")
def export_decision(ctx: Annotated[TenantContext, viewer], decision_id: uuid.UUID, format: Literal["md", "csv", "scores_csv"] = "md") -> Response:
    with ctx.session() as s:
        d = service.get_doc(s, decision_id)
        if not d.evidence_snapshot or d.snapshot_taken_at is None:
            raise ApiError(409, "no_snapshot", "Es gibt noch keinen eingefrorenen Belegstand zum Exportieren")
        evidence = export.stored_evidence(s, d)
        names, comments = _comments_for_export(ctx, s, d)
        tenant_tz = ctx.timezone
        if format == "md":
            body, media, ext = export.to_markdown(d, evidence, names, comments, tenant_tz), "text/markdown; charset=utf-8", "md"
        elif format == "csv":
            body, media, ext = export.evidence_csv(d, evidence), "text/csv; charset=utf-8", "csv"
        else:
            body, media, ext = export.scores_csv(d), "text/csv; charset=utf-8", "csv"
        ctx.audit(s, "export.decision", "decision_document", d.id, format=format, revision=d.revision, state=d.state, evidence=len(evidence))
        fname = f"entscheidung-r{d.revision}-{'scores' if format == 'scores_csv' else 'belege' if format == 'csv' else 'vorlage'}.{ext}"
    return Response(body, media_type=media, headers={"Content-Disposition": f'attachment; filename="{fname}"'})


