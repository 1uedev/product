from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from decision_evidence.db import models as m
from decision_evidence.errors import bad_request, not_found
from decision_evidence.identity import repository as identity
from decision_evidence.modules.common import (
    Page, Paging, check_version, etag, require_if_match, tenant_settings, to_csv,
)
from decision_evidence.modules.metrics import domain as md
from decision_evidence.modules.metrics import service as metrics_service
from decision_evidence.modules.problems import service
from decision_evidence.tenancy.context import TenantContext, editor, viewer

router = APIRouter(prefix="/api/v1/workspaces/{tenant_id}", tags=["problems"])
Status = Literal["proposed", "confirmed", "archived"]
Relation = Literal["supports", "contradicts", "context"]


class ProblemListItem(BaseModel):
    id: uuid.UUID
    title: str
    status: str
    origin: str
    owner_user_id: uuid.UUID | None
    owner_name: str | None
    supporting_customers: int
    supporting_statements: int
    contradicting_statements: int
    verified_evidence: int
    latest_evidence_at: datetime | None
    initiative_count: int
    demo_ai: bool
    version: int
    updated_at: datetime


class EvidenceOut(BaseModel):
    id: uuid.UUID
    relation: str
    quote: str
    origin: str
    human_verified: bool
    chunk_id: uuid.UUID
    chunk_text: str
    locator: dict[str, Any]
    source_record_id: uuid.UUID
    source_title: str
    source_origin: str
    file_id: uuid.UUID | None
    file_name: str | None
    feedback_id: uuid.UUID
    channel: str
    occurred_at: datetime
    customer_id: uuid.UUID | None
    customer_name: str | None
    customer_segment: str | None
    opportunity_id: uuid.UUID | None
    opportunity_name: str | None
    opportunity_stage: str | None


class HistoryOut(BaseModel):
    id: uuid.UUID
    action: str
    actor_name: str | None
    details: dict[str, Any]
    created_at: datetime


class AiInfo(BaseModel):
    provider: str
    model: str
    demo: bool
    created_at: datetime


class ProblemDetail(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    status: str
    origin: str
    owner_user_id: uuid.UUID | None
    owner_name: str | None
    version: int
    created_at: datetime
    updated_at: datetime
    merged_into_problem_id: uuid.UUID | None
    ai: AiInfo | None
    evidence: list[EvidenceOut]
    metrics: dict[str, Any]
    result_ids: dict[str, dict[str, Any]]
    history: list[HistoryOut]
    initiatives: list[dict[str, Any]]


def _owner_names(ctx: TenantContext, ids: set[uuid.UUID]) -> dict[uuid.UUID, str | None]:
    return {k: v["display_name"] or v["email"] for k, v in identity.user_names(ctx.tenant_id, ids).items()}


@router.get("/problems", response_model=Page[ProblemListItem])
def list_problems(ctx: Annotated[TenantContext, viewer], paging: Annotated[Paging, Depends()], status: Status | None = None,
                  q: Annotated[str | None, Query(max_length=200)] = None, owner_user_id: uuid.UUID | None = None,
                  origin: Literal["ai", "manual", "split"] | None = None,
                  sort: Literal["customers", "statements", "updated", "title"] = "customers",
                  include_archived: bool = False) -> Page[ProblemListItem]:
    with ctx.session() as s:
        cond = []
        if status:
            cond.append(m.Problem.status == status)
        elif not include_archived:
            cond.append(m.Problem.status != "archived")
        if q:
            cond.append(m.Problem.title.ilike(f"%{q.replace('%', '').replace('_', ' ')}%"))
        if owner_user_id:
            cond.append(m.Problem.owner_user_id == owner_user_id)
        if origin:
            cond.append(m.Problem.origin == origin)
        total = s.scalar(select(func.count()).select_from(m.Problem).where(*cond)) or 0
        rows = list(s.scalars(select(m.Problem).where(*cond).order_by(m.Problem.updated_at.desc(), m.Problem.id)))
        stats = metrics_service.list_stats(s, [r.id for r in rows])
        init_counts = dict(s.execute(select(m.Initiative.problem_id, func.count()).group_by(m.Initiative.problem_id)).all())
        mock_runs = set(s.scalars(select(m.AiRun.id).where(m.AiRun.provider == "mock")))
        key = {"customers": lambda p: (-stats.get(p.id, {}).get("customers", 0), -stats.get(p.id, {}).get("statements", 0), p.title.lower(), str(p.id)),
               "statements": lambda p: (-stats.get(p.id, {}).get("statements", 0), p.title.lower(), str(p.id)),
               "updated": lambda p: (-p.updated_at.timestamp(), str(p.id)),
               "title": lambda p: (p.title.lower(), str(p.id))}[sort]
        rows.sort(key=key)
        page_rows = rows[paging.offset:paging.offset + paging.limit]
        owners = _owner_names(ctx, {r.owner_user_id for r in page_rows if r.owner_user_id})
        items = []
        for p in page_rows:
            st = stats.get(p.id, {})
            items.append(ProblemListItem(
                id=p.id, title=p.title, status=p.status, origin=p.origin, owner_user_id=p.owner_user_id, owner_name=owners.get(p.owner_user_id) if p.owner_user_id else None,
                supporting_customers=st.get("customers", 0), supporting_statements=st.get("statements", 0), contradicting_statements=st.get("contradictions", 0),
                verified_evidence=st.get("verified", 0), latest_evidence_at=st.get("latest"), initiative_count=init_counts.get(p.id, 0),
                demo_ai=p.ai_run_id in mock_runs, version=p.version, updated_at=p.updated_at))
        return Page[ProblemListItem](items=items, total=total, limit=paging.limit, offset=paging.offset)


def build_detail(ctx: TenantContext, s: Any, problem_id: uuid.UUID) -> ProblemDetail:
    p = service.get_problem(s, problem_id)
    ts = tenant_settings(s)
    metrics, rows = metrics_service.problem_metrics(s, p.id, ts.evidence_fresh_days)
    history = list(s.scalars(select(m.ProblemHistory).where(m.ProblemHistory.problem_id == p.id).order_by(m.ProblemHistory.created_at.desc()).limit(100)))
    actor_ids = {h.actor_user_id for h in history if h.actor_user_id} | ({p.owner_user_id} if p.owner_user_id else set())
    names = _owner_names(ctx, actor_ids)
    run = s.get(m.AiRun, p.ai_run_id) if p.ai_run_id else None
    inits = [{"id": str(i.id), "title": i.title, "status": i.status} for i in s.scalars(select(m.Initiative).where(m.Initiative.problem_id == p.id).order_by(m.Initiative.created_at))]
    return ProblemDetail(
        id=p.id, title=p.title, description=p.description, status=p.status, origin=p.origin, owner_user_id=p.owner_user_id,
        owner_name=names.get(p.owner_user_id) if p.owner_user_id else None, version=p.version, created_at=p.created_at,
        updated_at=p.updated_at, merged_into_problem_id=p.merged_into_problem_id,
        ai=AiInfo(provider=run.provider, model=run.model, demo=run.provider == "mock", created_at=run.created_at) if run else None,
        evidence=[EvidenceOut(**r) for r in rows], metrics=metrics.to_dict(), result_ids=_jsonable(md.result_catalog(p.id, metrics)),
        history=[HistoryOut(id=h.id, action=h.action, actor_name=names.get(h.actor_user_id) if h.actor_user_id else None, details=h.details,
                            created_at=h.created_at) for h in history],
        initiatives=inits)


def _jsonable(x: Any) -> Any:
    import json
    return json.loads(json.dumps(x, default=str))


@router.get("/problems/{problem_id}", response_model=ProblemDetail)
def get_problem(ctx: Annotated[TenantContext, viewer], problem_id: uuid.UUID, response: Response) -> ProblemDetail:
    with ctx.session() as s:
        detail = build_detail(ctx, s, problem_id)
    response.headers["ETag"] = etag(detail.version)
    return detail


class ProblemCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=5000)


@router.post("/problems", response_model=ProblemDetail, status_code=201)
def create_problem(ctx: Annotated[TenantContext, editor], body: ProblemCreate) -> ProblemDetail:
    with ctx.session() as s:
        p = m.Problem(title=body.title.strip(), description=body.description, origin="manual", status="proposed", created_by=ctx.user_id)
        s.add(p)
        s.flush()
        service.log_history(s, p.id, "created", ctx.user_id)
        ctx.audit(s, "problem.created", "problem", p.id)
        return build_detail(ctx, s, p.id)


class ProblemPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    description: str | None = Field(default=None, max_length=5000)
    status: Status | None = None
    owner_user_id: uuid.UUID | None = None
    clear_owner: bool = False


@router.patch("/problems/{problem_id}", response_model=ProblemDetail)
def patch_problem(ctx: Annotated[TenantContext, editor], problem_id: uuid.UUID, body: ProblemPatch, expected: Annotated[int, Depends(require_if_match)]) -> ProblemDetail:
    with ctx.session() as s:
        p = service.get_problem(s, problem_id, lock=True)
        check_version(p.version, expected)
        changes: dict[str, Any] = {}
        if body.title is not None and body.title != p.title:
            changes["title"] = [p.title, body.title]
            p.title = body.title.strip()
        if body.description is not None and body.description != p.description:
            changes["description"] = True
            p.description = body.description
        if body.owner_user_id is not None:
            if not identity.is_active_member(ctx.tenant_id, body.owner_user_id):
                raise bad_request("owner_not_member", "Die Person ist kein aktives Mitglied des Workspaces")
            p.owner_user_id = body.owner_user_id
            changes["owner"] = str(body.owner_user_id)
        elif body.clear_owner:
            p.owner_user_id = None
            changes["owner"] = None
        if body.status is not None and body.status != p.status:
            if p.status == "archived" and p.merged_into_problem_id:
                raise bad_request("merged_problem", "Zusammengeführte Probleme können nicht reaktiviert werden")
            service.log_history(s, p.id, "status_changed", ctx.user_id, old=p.status, new=body.status)
            p.status = body.status
            ctx.audit(s, f"problem.{body.status}", "problem", p.id)
        if changes:
            service.log_history(s, p.id, "edited", ctx.user_id, **{k: v for k, v in changes.items()})
        service.bump(p)
        s.flush()
        return build_detail(ctx, s, p.id)


@router.delete("/problems/{problem_id}", status_code=204)
def delete_problem(ctx: Annotated[TenantContext, editor], problem_id: uuid.UUID) -> Response:
    with ctx.session() as s:
        service.delete_problem(s, problem_id)
        ctx.audit(s, "problem.deleted", "problem", problem_id)
    return Response(status_code=204)


class MergeIn(BaseModel):
    target_id: uuid.UUID
    source_ids: list[uuid.UUID] = Field(min_length=1, max_length=20)


@router.post("/problems/merge", response_model=ProblemDetail)
def merge(ctx: Annotated[TenantContext, editor], body: MergeIn) -> ProblemDetail:
    with ctx.session() as s:
        target = service.merge_problems(s, body.target_id, body.source_ids, ctx.user_id)
        ctx.audit(s, "problem.merged", "problem", target.id, sources=[str(x) for x in body.source_ids])
        return build_detail(ctx, s, target.id)


class SplitIn(BaseModel):
    evidence_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=5000)


@router.post("/problems/{problem_id}/split", response_model=ProblemDetail, status_code=201)
def split(ctx: Annotated[TenantContext, editor], problem_id: uuid.UUID, body: SplitIn) -> ProblemDetail:
    with ctx.session() as s:
        new = service.split_problem(s, problem_id, body.evidence_ids, body.title.strip(), body.description, ctx.user_id)
        ctx.audit(s, "problem.split", "problem", problem_id, new_problem=str(new.id))
        return build_detail(ctx, s, new.id)


class EvidenceCreate(BaseModel):
    source_chunk_id: uuid.UUID
    relation: Relation = "supports"
    quote: str | None = Field(default=None, max_length=600)


@router.post("/problems/{problem_id}/evidence", response_model=ProblemDetail, status_code=201)
def add_evidence(ctx: Annotated[TenantContext, editor], problem_id: uuid.UUID, body: EvidenceCreate) -> ProblemDetail:
    with ctx.session() as s:
        p = service.get_problem(s, problem_id, lock=True)
        service.add_evidence(s, p, body.source_chunk_id, body.relation, body.quote, ctx.user_id)
        return build_detail(ctx, s, p.id)


class EvidencePatch(BaseModel):
    relation: Relation | None = None
    human_verified: bool | None = None
    move_to_problem_id: uuid.UUID | None = None


@router.patch("/evidence/{evidence_id}", response_model=ProblemDetail)
def patch_evidence(ctx: Annotated[TenantContext, editor], evidence_id: uuid.UUID, body: EvidencePatch) -> ProblemDetail:
    with ctx.session() as s:
        ev = service.update_evidence(s, evidence_id, ctx.user_id, body.relation, body.human_verified, body.move_to_problem_id)
        ctx.audit(s, "evidence.corrected", "problem_evidence", evidence_id, relation=body.relation, verified=body.human_verified,
                  moved_to=str(body.move_to_problem_id) if body.move_to_problem_id else None)
        return build_detail(ctx, s, ev.problem_id)


@router.delete("/evidence/{evidence_id}", response_model=ProblemDetail)
def delete_evidence(ctx: Annotated[TenantContext, editor], evidence_id: uuid.UUID) -> ProblemDetail:
    with ctx.session() as s:
        ev = s.get(m.ProblemEvidence, evidence_id)
        if ev is None:
            raise not_found("Beleg")
        pid = ev.problem_id
        service.remove_evidence(s, evidence_id, ctx.user_id)
        ctx.audit(s, "evidence.removed", "problem_evidence", evidence_id)
        return build_detail(ctx, s, pid)


@router.get("/exports/problems.csv")
def export_problems(ctx: Annotated[TenantContext, viewer]) -> Response:
    with ctx.session() as s:
        problems = list(s.scalars(select(m.Problem).where(m.Problem.status != "archived").order_by(m.Problem.title)))
        stats = metrics_service.list_stats(s, [p.id for p in problems])
        customers, opps = metrics_service.load_universe(s)
        fresh = tenant_settings(s).evidence_fresh_days
        rows = []
        for p in problems:
            metrics, _ = metrics_service.problem_metrics(s, p.id, fresh, universe=(customers, opps))
            md_ = metrics.to_dict()
            fmt = lambda blk: "; ".join(f"{v} {c}" for c, v in blk["by_currency"].items()) or ""  # noqa: E731
            rows.append([str(p.id), p.title, p.status, md_["supporting_customers"], md_["supporting_statements"], md_["contradicting_statements"],
                         fmt(md_["arr"]), fmt(md_["annual_sales"]), fmt(md_["pipeline_attributed"]), fmt(md_["lost_attributed"]),
                         md_["customers_without_value"], md_["age"]["median_days"], md_["coverage"]["affected_share"]])
        ctx.audit(s, "export.problems", "problem", None, rows=len(rows))
    body = to_csv(["id", "titel", "status", "eindeutige_kunden", "aussagen", "widersprechende_aussagen", "arr_je_waehrung", "jahresumsatz_je_waehrung",
                   "offene_pipeline_zugeordnet", "verlorenes_volumen_zugeordnet", "kunden_ohne_wert", "median_alter_tage", "anteil_betroffener_kunden"], rows)
    return Response(body, media_type="text/csv; charset=utf-8", headers={"Content-Disposition": 'attachment; filename="probleme.csv"'})


