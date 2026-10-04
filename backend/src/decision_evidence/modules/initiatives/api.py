from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select

from decision_evidence.db import models as m
from decision_evidence.errors import bad_request, conflict, not_found
from decision_evidence.identity import repository as identity
from decision_evidence.modules.common import (
    Page,
    Paging,
    check_version,
    etag,
    require_if_match,
    tenant_settings,
)
from decision_evidence.modules.metrics import service as metrics_service
from decision_evidence.modules.scoring import domain as sd
from decision_evidence.modules.scoring import service as scoring_service
from decision_evidence.tenancy.context import TenantContext, editor, viewer

router = APIRouter(prefix="/api/v1/workspaces/{tenant_id}", tags=["initiatives"])
IStatus = Literal["proposed", "evaluating", "decided", "dropped"]
Unit = Literal["person_days", "person_weeks"]


class AssumptionOut(BaseModel):
    id: uuid.UUID
    statement: str
    kind: str
    source_chunk_id: uuid.UUID | None
    source_quote: str | None
    owner_user_id: uuid.UUID | None
    validation_status: str


class InitiativeOut(BaseModel):
    id: uuid.UUID
    problem_id: uuid.UUID
    problem_title: str
    title: str
    desired_outcome: str
    target_segment: str | None
    effort_low: Decimal | None
    effort_high: Decimal | None
    effort_unit: str
    status: str
    owner_user_id: uuid.UUID | None
    version: int
    created_at: datetime
    updated_at: datetime
    assumption_count: int = 0
    open_assumptions: int = 0
    latest_decision: dict[str, Any] | None = None


class InitiativeDetail(InitiativeOut):
    assumptions: list[AssumptionOut]
    decisions: list[dict[str, Any]]
    problem: dict[str, Any]
    score: dict[str, Any] | None


def _base(i: m.Initiative, ptitle: str) -> dict[str, Any]:
    return dict(id=i.id, problem_id=i.problem_id, problem_title=ptitle, title=i.title, desired_outcome=i.desired_outcome, target_segment=i.target_segment,
                effort_low=i.effort_low, effort_high=i.effort_high, effort_unit=i.effort_unit, status=i.status, owner_user_id=i.owner_user_id,
                version=i.version, created_at=i.created_at, updated_at=i.updated_at)


@router.get("/initiatives", response_model=Page[InitiativeOut])
def list_initiatives(ctx: Annotated[TenantContext, viewer], paging: Annotated[Paging, Depends()], status: IStatus | None = None,
                     problem_id: uuid.UUID | None = None) -> Page[InitiativeOut]:
    with ctx.session() as s:
        cond = []
        if status:
            cond.append(m.Initiative.status == status)
        if problem_id:
            cond.append(m.Initiative.problem_id == problem_id)
        total = s.scalar(select(func.count()).select_from(m.Initiative).where(*cond)) or 0
        rows = s.execute(select(m.Initiative, m.Problem.title).join(m.Problem, m.Problem.id == m.Initiative.problem_id).where(*cond)
                         .order_by(m.Initiative.updated_at.desc(), m.Initiative.id).limit(paging.limit).offset(paging.offset)).all()
        ids = [i.id for i, _ in rows]
        counts: dict[uuid.UUID, tuple[int, int]] = {r[0]: (r[1], r[2]) for r in s.execute(
            select(m.InitiativeAssumption.initiative_id, func.count(), func.count().filter(m.InitiativeAssumption.validation_status == "open"))
            .where(m.InitiativeAssumption.initiative_id.in_(ids)).group_by(m.InitiativeAssumption.initiative_id))}
        latest: dict[uuid.UUID, dict[str, Any]] = {}
        for d in s.scalars(select(m.DecisionDocument).where(m.DecisionDocument.initiative_id.in_(ids)).order_by(m.DecisionDocument.revision)):
            latest[d.initiative_id] = {"id": str(d.id), "revision": d.revision, "state": d.state}
        items = [InitiativeOut(**_base(i, pt), assumption_count=counts.get(i.id, (0, 0))[0], open_assumptions=counts.get(i.id, (0, 0))[1], latest_decision=latest.get(i.id))
                 for i, pt in rows]
        return Page[InitiativeOut](items=items, total=total, limit=paging.limit, offset=paging.offset)


class InitiativeCreate(BaseModel):
    problem_id: uuid.UUID
    title: str = Field(min_length=1, max_length=300)
    desired_outcome: str = Field(default="", max_length=5000)
    target_segment: str | None = Field(default=None, max_length=200)
    effort_low: Decimal | None = Field(default=None, ge=0, le=100000)
    effort_high: Decimal | None = Field(default=None, ge=0, le=100000)
    effort_unit: Unit = "person_days"
    owner_user_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _range(self) -> InitiativeCreate:
        if self.effort_low is not None and self.effort_high is not None and self.effort_low > self.effort_high:
            raise ValueError("effort_low darf nicht größer als effort_high sein")
        return self


def _check_owner(ctx: TenantContext, owner: uuid.UUID | None) -> None:
    if owner and not identity.is_active_member(ctx.tenant_id, owner):
        raise bad_request("owner_not_member", "Die Person ist kein aktives Mitglied des Workspaces")


@router.post("/initiatives", response_model=InitiativeDetail, status_code=201)
def create_initiative(ctx: Annotated[TenantContext, editor], body: InitiativeCreate) -> InitiativeDetail:
    _check_owner(ctx, body.owner_user_id)
    with ctx.session() as s:
        p = s.get(m.Problem, body.problem_id)
        if p is None:
            raise not_found("Problem")
        if p.status == "archived":
            raise conflict("problem_archived", "Für archivierte Probleme können keine Initiativen angelegt werden")
        i = m.Initiative(problem_id=p.id, title=body.title.strip(), desired_outcome=body.desired_outcome, target_segment=body.target_segment,
                         effort_low=body.effort_low, effort_high=body.effort_high, effort_unit=body.effort_unit, owner_user_id=body.owner_user_id, created_by=ctx.user_id)
        s.add(i)
        s.flush()
        ctx.audit(s, "initiative.created", "initiative", i.id, problem_id=str(p.id))
        return build_detail(ctx, s, i.id)


def build_detail(ctx: TenantContext, s: Any, initiative_id: uuid.UUID) -> InitiativeDetail:
    i = s.get(m.Initiative, initiative_id)
    if i is None:
        raise not_found("Initiative")
    p = s.get(m.Problem, i.problem_id)
    rows = s.execute(select(m.InitiativeAssumption, m.SourceChunk.text).outerjoin(m.SourceChunk, m.SourceChunk.id == m.InitiativeAssumption.source_chunk_id)
                     .where(m.InitiativeAssumption.initiative_id == i.id).order_by(m.InitiativeAssumption.created_at, m.InitiativeAssumption.id)).all()
    assumptions = [AssumptionOut(id=a.id, statement=a.statement, kind=a.kind, source_chunk_id=a.source_chunk_id, source_quote=(t[:300] if t else None),
                                 owner_user_id=a.owner_user_id, validation_status=a.validation_status) for a, t in rows]
    decisions = [{"id": str(d.id), "revision": d.revision, "state": d.state, "title": d.title, "approved_at": d.approved_at.isoformat() if d.approved_at else None,
                  "updated_at": d.updated_at.isoformat()} for d in s.scalars(select(m.DecisionDocument).where(m.DecisionDocument.initiative_id == i.id).order_by(m.DecisionDocument.revision.desc()))]
    ts = tenant_settings(s)
    universe = metrics_service.load_universe(s)
    metrics, _ = metrics_service.problem_metrics(s, p.id, ts.evidence_fresh_days, universe=universe)
    pol_row = scoring_service.ensure_default_policy(s)
    policy = scoring_service.to_policy(pol_row)
    inp = scoring_service.score_input(i.title, metrics, scoring_service.universe_totals(*universe), policy.parameters, effort=(i.effort_low, i.effort_high, i.effort_unit))
    score = sd.score(policy, inp).to_dict() | {"policy": scoring_service.policy_snapshot(pol_row)}
    open_n = sum(1 for a in assumptions if a.validation_status == "open")
    return InitiativeDetail(**_base(i, p.title), assumption_count=len(assumptions), open_assumptions=open_n, latest_decision=decisions[0] if decisions else None,
                            assumptions=assumptions, decisions=decisions,
                            problem={"id": str(p.id), "title": p.title, "status": p.status, "unique_customers": metrics.supporting_customers,
                                     "statements": metrics.supporting_statements, "warnings": metrics.warnings}, score=score)


@router.get("/initiatives/{initiative_id}", response_model=InitiativeDetail)
def get_initiative(ctx: Annotated[TenantContext, viewer], initiative_id: uuid.UUID, response: Response) -> InitiativeDetail:
    with ctx.session() as s:
        d = build_detail(ctx, s, initiative_id)
    response.headers["ETag"] = etag(d.version)
    return d


class InitiativePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    desired_outcome: str | None = Field(default=None, max_length=5000)
    target_segment: str | None = Field(default=None, max_length=200)
    effort_low: Decimal | None = Field(default=None, ge=0, le=100000)
    effort_high: Decimal | None = Field(default=None, ge=0, le=100000)
    effort_unit: Unit | None = None
    status: IStatus | None = None
    owner_user_id: uuid.UUID | None = None
    clear_effort: bool = False


@router.patch("/initiatives/{initiative_id}", response_model=InitiativeDetail)
def patch_initiative(ctx: Annotated[TenantContext, editor], initiative_id: uuid.UUID, body: InitiativePatch, expected: Annotated[int, Depends(require_if_match)]) -> InitiativeDetail:
    _check_owner(ctx, body.owner_user_id)
    with ctx.session() as s:
        i = s.scalar(select(m.Initiative).where(m.Initiative.id == initiative_id).with_for_update())
        if i is None:
            raise not_found("Initiative")
        check_version(i.version, expected)
        for f in ("title", "desired_outcome", "target_segment", "effort_unit", "owner_user_id"):
            v = getattr(body, f)
            if v is not None:
                setattr(i, f, v)
        if body.clear_effort:
            i.effort_low = i.effort_high = None
        else:
            if body.effort_low is not None:
                i.effort_low = body.effort_low
            if body.effort_high is not None:
                i.effort_high = body.effort_high
        if i.effort_low is not None and i.effort_high is not None and i.effort_low > i.effort_high:
            raise bad_request("effort_range", "Aufwand von darf nicht größer als Aufwand bis sein")
        if body.status is not None and body.status != i.status:
            i.status = body.status
            ctx.audit(s, "initiative.status_changed", "initiative", i.id, status=body.status)
        i.version += 1
        s.flush()
        return build_detail(ctx, s, i.id)


@router.delete("/initiatives/{initiative_id}", status_code=204)
def delete_initiative(ctx: Annotated[TenantContext, editor], initiative_id: uuid.UUID) -> Response:
    with ctx.session() as s:
        i = s.get(m.Initiative, initiative_id)
        if i is None:
            raise not_found("Initiative")
        if s.scalar(select(func.count()).select_from(m.DecisionDocument).where(m.DecisionDocument.initiative_id == i.id)):
            raise conflict("initiative_has_decisions", "Die Initiative hat Entscheidungsdokumente", "Bitte stattdessen den Status auf „verworfen“ setzen.")
        s.delete(i)
        ctx.audit(s, "initiative.deleted", "initiative", initiative_id)
    return Response(status_code=204)


class AssumptionIn(BaseModel):
    statement: str = Field(min_length=1, max_length=2000)
    kind: Literal["observed", "estimate", "hypothesis"]
    source_chunk_id: uuid.UUID | None = None
    owner_user_id: uuid.UUID | None = None
    validation_status: Literal["open", "validated", "refuted"] = "open"


@router.post("/initiatives/{initiative_id}/assumptions", response_model=InitiativeDetail, status_code=201)
def add_assumption(ctx: Annotated[TenantContext, editor], initiative_id: uuid.UUID, body: AssumptionIn) -> InitiativeDetail:
    _check_owner(ctx, body.owner_user_id)
    if body.kind == "observed" and body.source_chunk_id is None:
        raise bad_request("observed_needs_source", "Eine beobachtete Annahme braucht eine Quelle")
    with ctx.session() as s:
        if s.get(m.Initiative, initiative_id) is None:
            raise not_found("Initiative")
        if body.source_chunk_id and s.get(m.SourceChunk, body.source_chunk_id) is None:
            raise not_found("Quelle")
        a = m.InitiativeAssumption(initiative_id=initiative_id, statement=body.statement.strip(), kind=body.kind, source_chunk_id=body.source_chunk_id,
                                   owner_user_id=body.owner_user_id, validation_status=body.validation_status)
        s.add(a)
        s.flush()
        ctx.audit(s, "assumption.created", "initiative_assumption", a.id)
        return build_detail(ctx, s, initiative_id)


class AssumptionPatch(BaseModel):
    statement: str | None = Field(default=None, min_length=1, max_length=2000)
    kind: Literal["observed", "estimate", "hypothesis"] | None = None
    source_chunk_id: uuid.UUID | None = None
    owner_user_id: uuid.UUID | None = None
    validation_status: Literal["open", "validated", "refuted"] | None = None


@router.patch("/assumptions/{assumption_id}", response_model=InitiativeDetail)
def patch_assumption(ctx: Annotated[TenantContext, editor], assumption_id: uuid.UUID, body: AssumptionPatch) -> InitiativeDetail:
    _check_owner(ctx, body.owner_user_id)
    with ctx.session() as s:
        a = s.get(m.InitiativeAssumption, assumption_id)
        if a is None:
            raise not_found("Annahme")
        for f in ("statement", "kind", "source_chunk_id", "owner_user_id", "validation_status"):
            v = getattr(body, f)
            if v is not None:
                setattr(a, f, v)
        if a.kind == "observed" and a.source_chunk_id is None:
            raise bad_request("observed_needs_source", "Eine beobachtete Annahme braucht eine Quelle")
        s.flush()
        ctx.audit(s, "assumption.updated", "initiative_assumption", a.id, validation_status=a.validation_status)
        return build_detail(ctx, s, a.initiative_id)


@router.delete("/assumptions/{assumption_id}", response_model=InitiativeDetail)
def delete_assumption(ctx: Annotated[TenantContext, editor], assumption_id: uuid.UUID) -> InitiativeDetail:
    with ctx.session() as s:
        a = s.get(m.InitiativeAssumption, assumption_id)
        if a is None:
            raise not_found("Annahme")
        iid = a.initiative_id
        s.delete(a)
        s.flush()
        ctx.audit(s, "assumption.deleted", "initiative_assumption", assumption_id)
        return build_detail(ctx, s, iid)
