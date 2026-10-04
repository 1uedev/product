from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from decision_evidence.db import models as m
from decision_evidence.errors import bad_request
from decision_evidence.modules.common import tenant_settings
from decision_evidence.modules.metrics import domain as md
from decision_evidence.modules.metrics import service as metrics_service
from decision_evidence.modules.scoring import domain as sd
from decision_evidence.modules.scoring import service
from decision_evidence.tenancy.context import TenantContext, admin, viewer

router = APIRouter(prefix="/api/v1/workspaces/{tenant_id}", tags=["scoring"])


class PolicyOut(BaseModel):
    id: uuid.UUID
    name: str
    formula_version: str
    weights: dict[str, Any]
    parameters: dict[str, Any]
    missing_value_policy: str
    active: bool
    criteria: dict[str, str] = sd.CRITERION_LABELS


def _out(p: m.ScoringPolicy) -> PolicyOut:
    return PolicyOut(id=p.id, name=p.name, formula_version=p.formula_version, weights=p.weights, parameters=p.parameters,
                     missing_value_policy=p.missing_value_policy, active=p.active)


@router.get("/scoring-policies", response_model=list[PolicyOut])
def list_policies(ctx: Annotated[TenantContext, viewer]) -> list[PolicyOut]:
    with ctx.session() as s:
        service.ensure_default_policy(s, ctx.user_id)
        return [_out(p) for p in s.scalars(select(m.ScoringPolicy).order_by(m.ScoringPolicy.created_at, m.ScoringPolicy.id))]


class PolicyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    weights: dict[str, Decimal]
    missing_value_policy: str = "exclude"
    currency: str = Field(default="EUR", pattern=r"^[A-Z]{3}$")
    effort_reference_days: int = Field(default=60, ge=1, le=3650)
    activate: bool = False


@router.post("/scoring-policies", response_model=PolicyOut, status_code=201)
def create_policy(ctx: Annotated[TenantContext, admin], body: PolicyIn) -> PolicyOut:
    service.validate_policy_input(body.weights, body.missing_value_policy)
    with ctx.session() as s:
        p = m.ScoringPolicy(name=body.name, weights={k: float(v) for k, v in body.weights.items()}, missing_value_policy=body.missing_value_policy,
                            parameters={"currency": body.currency, "effort_reference_days": body.effort_reference_days,
                                        "fresh_days": tenant_settings(s).evidence_fresh_days}, created_by=ctx.user_id, active=False)
        s.add(p)
        s.flush()
        if body.activate:
            service.activate(s, p.id)
        ctx.audit(s, "scoring_policy.created", "scoring_policy", p.id, activated=body.activate)
        return _out(p)


@router.post("/scoring-policies/{policy_id}/activate", response_model=PolicyOut)
def activate_policy(ctx: Annotated[TenantContext, admin], policy_id: uuid.UUID) -> PolicyOut:
    with ctx.session() as s:
        p = service.activate(s, policy_id)
        ctx.audit(s, "scoring_policy.activated", "scoring_policy", p.id)
        return _out(p)


class CompareIn(BaseModel):
    initiative_ids: list[uuid.UUID] = Field(min_length=1, max_length=12)
    policy_id: uuid.UUID | None = None
    weights_override: dict[str, Decimal] | None = None


@router.post("/scoring/compare")
def compare(ctx: Annotated[TenantContext, viewer], body: CompareIn) -> dict[str, Any]:
    """Scores initiatives with the (optionally overridden) policy. The override is never stored; it lets users explore weights."""
    with ctx.session() as s:
        pol_row = service.get_policy(s, body.policy_id)
        policy = service.to_policy(pol_row)
        if body.weights_override is not None:
            policy = sd.Policy(body.weights_override, policy.missing_value_policy, policy.parameters)
            problems = policy.validate()
            if problems:
                raise bad_request("weights_invalid", "Ungültige Gewichte", " ".join(problems))
        fresh = tenant_settings(s).evidence_fresh_days
        universe = metrics_service.load_universe(s)
        totals = service.universe_totals(*universe)
        inits = list(s.scalars(select(m.Initiative).where(m.Initiative.id.in_(body.initiative_ids))))
        by_id = {i.id: i for i in inits}
        inputs: list[sd.ScoreInput] = []
        summaries: list[dict[str, Any]] = []
        per_problem: dict[uuid.UUID, frozenset[uuid.UUID]] = {}
        titles = {i.id: i.title for i in inits}
        for iid in body.initiative_ids:
            ini = by_id.get(iid)
            if ini is None:
                continue
            metrics, _rows = metrics_service.problem_metrics(s, ini.problem_id, fresh, universe=universe)
            per_problem[ini.problem_id] = metrics.affected_customer_ids
            name = ini.title if list(titles.values()).count(ini.title) == 1 else f"{ini.title} ({str(ini.id)[:4]})"
            inputs.append(service.score_input(name, metrics, totals, policy.parameters, effort=(ini.effort_low, ini.effort_high, ini.effort_unit)))
            summaries.append({"initiative_id": str(ini.id), "name": name, "problem_id": str(ini.problem_id), "unique_customers": metrics.supporting_customers,
                              "statements": metrics.supporting_statements, "arr": metrics.arr.as_dict(), "pipeline_attributed": metrics.pipeline_attributed.as_dict(),
                              "warnings": metrics.warnings})
        results = [sd.score(policy, i) for i in inputs]
        customers, opps = universe
        return {
            "policy": service.policy_snapshot(pol_row) | ({"weights_overridden": True, "weights": {k: str(v) for k, v in policy.weights.items()}} if body.weights_override else {}),
            "results": [r.to_dict() for r in results], "ranking": sd.rank(results), "sensitivity": sd.sensitivity(policy, inputs),
            "initiatives": summaries, "portfolio": md.portfolio_totals(per_problem, customers, opps),
            "method": "Gewichtete Summe normierter Kriterien (0..1). Fehlende Werte werden nach der Policy-Regel behandelt und ausgewiesen.",
        }
