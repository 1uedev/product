"""Builds score inputs from real metrics and manages scoring policies."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from decision_evidence.db import models as m
from decision_evidence.errors import conflict, not_found
from decision_evidence.modules.metrics import domain as md
from decision_evidence.modules.scoring import domain as sd

Q4 = Decimal("0.0001")


@dataclass
class Totals:
    customers: int
    arr: dict[str, Decimal]
    open_pipeline: dict[str, Decimal]


def universe_totals(customers: dict[uuid.UUID, md.CustomerRef], opps: dict[uuid.UUID, md.OpportunityRef]) -> Totals:
    arr: dict[str, Decimal] = {}
    for c in customers.values():
        if c.basis == "arr" and c.value is not None and c.currency:
            arr[c.currency] = arr.get(c.currency, Decimal(0)) + c.value
    pipe: dict[str, Decimal] = {}
    for o in opps.values():
        if o.stage == "open" and o.amount is not None and o.currency:
            pipe[o.currency] = pipe.get(o.currency, Decimal(0)) + o.amount
    return Totals(len(customers), arr, pipe)


def to_policy(row: m.ScoringPolicy) -> sd.Policy:
    return sd.Policy({k: Decimal(str(v)) for k, v in row.weights.items()}, row.missing_value_policy, dict(row.parameters or {}))


def policy_snapshot(row: m.ScoringPolicy) -> dict[str, Any]:
    return {"id": str(row.id), "name": row.name, "formula_version": row.formula_version, "weights": {k: str(v) for k, v in row.weights.items()},
            "parameters": row.parameters, "missing_value_policy": row.missing_value_policy}


def ensure_default_policy(s: Session, actor: uuid.UUID | None = None) -> m.ScoringPolicy:
    pol = s.scalar(select(m.ScoringPolicy).where(m.ScoringPolicy.active))
    if pol is None:
        pol = m.ScoringPolicy(name="Standard (Version 1)", weights={k: float(v) for k, v in sd.DEFAULT_WEIGHTS.items()}, parameters=dict(sd.DEFAULT_PARAMETERS),
                              missing_value_policy="exclude", active=True, created_by=actor)
        s.add(pol)
        s.flush()
    return pol


def get_policy(s: Session, policy_id: uuid.UUID | None) -> m.ScoringPolicy:
    if policy_id is None:
        return ensure_default_policy(s)
    pol = s.get(m.ScoringPolicy, policy_id)
    if pol is None:
        raise not_found("Scoring-Policy")
    return pol


def activate(s: Session, policy_id: uuid.UUID) -> m.ScoringPolicy:
    pol = s.get(m.ScoringPolicy, policy_id)
    if pol is None:
        raise not_found("Scoring-Policy")
    s.execute(update(m.ScoringPolicy).where(m.ScoringPolicy.active, m.ScoringPolicy.id != policy_id).values(active=False))
    pol.active = True
    s.flush()
    return pol


def validate_policy_input(weights: dict[str, Decimal], missing: str) -> None:
    problems = sd.Policy(weights, missing).validate()
    if problems:
        raise conflict("policy_invalid", "Die Scoring-Policy ist ungültig", " ".join(problems))


def score_input(name: str, metrics: md.ProblemMetrics, totals: Totals, params: dict[str, Any], *, effort: tuple[Decimal | None, Decimal | None, str] | None = None,
                risk_rating: str | None = None) -> sd.ScoreInput:
    cur = str(params.get("currency", "EUR"))
    notes: dict[str, str] = {}
    values: dict[str, Decimal | None] = {}
    values["customer_reach"] = (Decimal(metrics.supporting_customers) / totals.customers).quantize(Q4) if totals.customers else None
    notes["customer_reach"] = f"{metrics.supporting_customers} von {totals.customers} Kunden"
    got = metrics.arr.by_currency.get(cur, Decimal(0))
    tot = totals.arr.get(cur)
    values["arr_exposure"] = (got / tot).quantize(Q4) if tot else None
    other = sorted(set(metrics.arr.by_currency) - {cur})
    notes["arr_exposure"] = (f"{got} {cur} von {tot} {cur} bekanntem ARR" if tot else f"Kein bekannter ARR in {cur}") + (
        f"; andere Währungen ({', '.join(other)}) fließen nicht ein" if other else "") + (
        f"; {metrics.customers_without_value} betroffene Kunden ohne Wert" if metrics.customers_without_value else "")
    got_p = metrics.pipeline_attributed.by_currency.get(cur, Decimal(0))
    tot_p = totals.open_pipeline.get(cur)
    values["pipeline_at_stake"] = (got_p / tot_p).quantize(Q4) if tot_p else None
    notes["pipeline_at_stake"] = f"{got_p} {cur} von {tot_p} {cur} offener Pipeline (ausdrücklich zugeordnet)" if tot_p else f"Keine offene Pipeline in {cur}"
    if metrics.supporting_statements:
        parts = [x for x in (metrics.verified_share, metrics.fresh_share) if x is not None]
        values["evidence_quality"] = (sum(parts, Decimal(0)) / len(parts)).quantize(Q4)
        notes["evidence_quality"] = f"verifiziert {metrics.verified_share}, aktuell (≤ {metrics.fresh_days} Tage) {metrics.fresh_share}"
        denom = metrics.supporting_statements + metrics.contradicting_statements
        values["consistency"] = (Decimal(metrics.supporting_statements) / denom).quantize(Q4)
        notes["consistency"] = f"{metrics.supporting_statements} unterstützend, {metrics.contradicting_statements} widersprechend"
    else:
        values["evidence_quality"] = values["consistency"] = None
    if effort and (effort[0] is not None or effort[1] is not None):
        values["low_effort"] = sd.effort_value(effort[0], effort[1], effort[2], Decimal(str(params.get("effort_reference_days", 60))))
        notes["low_effort"] = f"{effort[0] or '?'}–{effort[1] or '?'} {effort[2]} relativ zu {params.get('effort_reference_days', 60)} Personentagen"
    else:
        values["low_effort"] = None
        notes["low_effort"] = "Kein Aufwand geschätzt"
    if risk_rating in sd.RISK_VALUES:
        values["low_risk"] = sd.RISK_VALUES[risk_rating]
        notes["low_risk"] = f"Risiko „{risk_rating}“ (menschliche Einschätzung)"
    else:
        values["low_risk"] = None
        notes["low_risk"] = "Risiko nicht eingeschätzt"
    return sd.ScoreInput(name, values, notes)
