"""Decision documents: snapshots, drift against the current knowledge state, and the review workflow."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from decision_evidence.db import models as m
from decision_evidence.errors import ApiError, bad_request, conflict, forbidden, not_found
from decision_evidence.modules.common import now, tenant_settings
from decision_evidence.modules.metrics import domain as md
from decision_evidence.modules.metrics import service as metrics_service
from decision_evidence.modules.scoring import domain as sd
from decision_evidence.modules.scoring import service as scoring_service

MAX_OPTIONS = 4


class OptionModel(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=2000)
    effort_low: Decimal | None = Field(default=None, ge=0, le=100000)
    effort_high: Decimal | None = Field(default=None, ge=0, le=100000)
    effort_unit: Literal["person_days", "person_weeks"] = "person_days"
    addressed_segments: list[str] = Field(default_factory=list, max_length=20)
    expected_effects: str = Field(default="", max_length=2000)
    risks: str = Field(default="", max_length=2000)
    risk_rating: Literal["low", "medium", "high"] | None = None

    @field_validator("effort_high")
    @classmethod
    def _range(cls, v: Decimal | None, info: Any) -> Decimal | None:
        low = info.data.get("effort_low")
        if v is not None and low is not None and low > v:
            raise ValueError("effort_low darf nicht größer als effort_high sein")
        return v


def normalise_options(raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(raw) > MAX_OPTIONS:
        raise bad_request("too_many_options", f"Höchstens {MAX_OPTIONS} Optionen")
    out = []
    for idx, o in enumerate(raw):
        d = OptionModel.model_validate(o).model_dump(mode="json")
        d["key"] = chr(ord("A") + idx)
        out.append(d)
    names = [o["name"].strip().casefold() for o in out]
    if len(set(names)) != len(names):
        raise bad_request("duplicate_option", "Optionsnamen müssen verschieden sein")
    return out


def get_doc(s: Session, doc_id: uuid.UUID, lock: bool = False) -> m.DecisionDocument:
    q = select(m.DecisionDocument).where(m.DecisionDocument.id == doc_id)
    if lock:
        q = q.with_for_update()
    d = s.scalar(q)
    if d is None:
        raise not_found("Entscheidungsvorlage")
    return d


def create_revision(s: Session, initiative_id: uuid.UUID, actor: uuid.UUID) -> m.DecisionDocument:
    ini = s.scalar(select(m.Initiative).where(m.Initiative.id == initiative_id).with_for_update())
    if ini is None:
        raise not_found("Initiative")
    open_doc = s.scalar(select(m.DecisionDocument).where(m.DecisionDocument.initiative_id == ini.id, m.DecisionDocument.state.in_(("draft", "in_review"))))
    if open_doc:
        raise conflict("revision_open", "Es gibt bereits eine offene Revision", "Bitte erst die offene Revision abschließen.", document_id=str(open_doc.id))
    latest = s.scalar(select(m.DecisionDocument).where(m.DecisionDocument.initiative_id == ini.id).order_by(m.DecisionDocument.revision.desc()).limit(1))
    revision = (latest.revision + 1) if latest else 1
    doc = m.DecisionDocument(initiative_id=ini.id, revision=revision, title=(latest.title if latest else f"Entscheidungsvorlage: {ini.title}"),
                             options=(latest.options if latest else []), recommendation_text=(latest.recommendation_text if latest else ""),
                             scoring_policy_id=None, state="draft", created_by=actor)
    s.add(doc)
    s.flush()
    return doc


def evidence_universe(s: Session, ini: m.Initiative) -> dict[str, Any]:
    ts = tenant_settings(s)
    universe = metrics_service.load_universe(s)
    metrics, rows = metrics_service.problem_metrics(s, ini.problem_id, ts.evidence_fresh_days, universe=universe)
    return {"ts": ts, "universe": universe, "metrics": metrics, "rows": rows, "totals": scoring_service.universe_totals(*universe)}


def build_snapshots(s: Session, doc: m.DecisionDocument, policy_row: m.ScoringPolicy) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    ini = s.get(m.Initiative, doc.initiative_id)
    assert ini is not None
    problem = s.get(m.Problem, ini.problem_id)
    assert problem is not None
    u = evidence_universe(s, ini)
    metrics: md.ProblemMetrics = u["metrics"]
    customers, opps = u["universe"]
    policy = scoring_service.to_policy(policy_row)
    catalog = md.result_catalog(problem.id, metrics)
    evidence = [{
        "evidence_id": str(r["id"]), "chunk_id": str(r["chunk_id"]), "relation": r["relation"], "quote": r["quote"], "human_verified": r["human_verified"],
        "customer_id": str(r["customer_id"]) if r["customer_id"] else None, "customer_name": r["customer_name"], "segment": r["customer_segment"],
        "occurred_at": r["occurred_at"].isoformat(), "channel": r["channel"], "locator": r["locator"], "source_title": r["source_title"],
        "file_name": r["file_name"], "source_origin": r["source_origin"]} for r in u["rows"]]
    taken = now()
    ev_snapshot = {
        "taken_at": taken.isoformat(), "problem": {"id": str(problem.id), "title": problem.title, "description": problem.description, "status": problem.status,
                                                    "version": problem.version},
        "initiative": {"id": str(ini.id), "title": ini.title, "desired_outcome": ini.desired_outcome, "target_segment": ini.target_segment,
                       "effort_low": str(ini.effort_low) if ini.effort_low is not None else None, "effort_high": str(ini.effort_high) if ini.effort_high is not None else None,
                       "effort_unit": ini.effort_unit},
        "metrics": metrics.to_dict(), "result_ids": _json(catalog), "evidence": evidence,
        "universe": {"customers": u["totals"].customers, "arr": {k: str(v) for k, v in u["totals"].arr.items()},
                     "open_pipeline": {k: str(v) for k, v in u["totals"].open_pipeline.items()}},
        "assumptions": [{"statement": a.statement, "kind": a.kind, "validation_status": a.validation_status}
                        for a in s.scalars(select(m.InitiativeAssumption).where(m.InitiativeAssumption.initiative_id == ini.id).order_by(m.InitiativeAssumption.created_at))],
    }
    inputs, option_meta = [], []
    refs = metrics_service.to_refs(u["rows"])
    for opt in doc.options:
        segs = {x for x in opt.get("addressed_segments", []) if x}
        scoped = refs if not segs else [e for e in refs if e.customer_id and (customers[e.customer_id].segment or "unbekannt") in segs]
        sm = md.compute_problem_metrics(scoped, customers, opps, fresh_days=u["ts"].evidence_fresh_days)
        name = f"{opt['key']}: {opt['name']}"
        inp = scoring_service.score_input(name, sm, u["totals"], policy.parameters, effort=(_dec(opt.get("effort_low")), _dec(opt.get("effort_high")), opt.get("effort_unit", "person_days")),
                                          risk_rating=opt.get("risk_rating"))
        inputs.append(inp)
        option_meta.append({"key": opt["key"], "name": opt["name"], "scope_segments": sorted(segs), "scope_customers": sm.supporting_customers,
                            "scope_statements": sm.supporting_statements,
                            "inputs": {k: (None if v is None else str(v)) for k, v in inp.values.items()}, "notes": inp.notes})
    results = [sd.score(policy, i) for i in inputs]
    initiative_input = scoring_service.score_input(ini.title, metrics, u["totals"], policy.parameters, effort=(ini.effort_low, ini.effort_high, ini.effort_unit))
    sc_snapshot = {
        "generated_at": taken.isoformat(), "policy": scoring_service.policy_snapshot(policy_row), "options": option_meta,
        "results": [r.to_dict() for r in results], "ranking": sd.rank(results), "sensitivity": sd.sensitivity(policy, inputs) if inputs else None,
        "initiative_score": sd.score(policy, initiative_input).to_dict(),
        "method": "Gewichtete Summe normierter Kriterien; Details je Kriterium siehe contributions. Keine Prognose von Mehrumsatz.",
    }
    rows = [{"source_chunk_id": r["chunk_id"], "relation": r["relation"], "quote": r["quote"]} for r in u["rows"]]
    return ev_snapshot, sc_snapshot, rows


def _dec(v: Any) -> Decimal | None:
    return None if v in (None, "") else Decimal(str(v))


def _json(x: Any) -> Any:
    import json
    return json.loads(json.dumps(x, default=str))


def refresh_snapshot(s: Session, doc: m.DecisionDocument) -> None:
    if doc.state != "draft":
        raise conflict("not_draft", "Nur Entwürfe können aktualisiert werden")
    policy_row = scoring_service.get_policy(s, doc.scoring_policy_id) if doc.scoring_policy_id else scoring_service.ensure_default_policy(s)
    ev_snapshot, sc_snapshot, rows = build_snapshots(s, doc, policy_row)
    s.execute(delete(m.DecisionEvidence).where(m.DecisionEvidence.decision_document_id == doc.id))
    if rows:
        s.execute(m.DecisionEvidence.__table__.insert(), [
            {"tenant_id": doc.tenant_id, "decision_document_id": doc.id, "source_chunk_id": r["source_chunk_id"], "relation": r["relation"], "quote": r["quote"]} for r in rows])
    doc.evidence_snapshot, doc.scoring_snapshot, doc.scoring_policy_id = ev_snapshot, sc_snapshot, policy_row.id
    doc.snapshot_taken_at = now()
    doc.version += 1
    s.flush()


def drift(s: Session, doc: m.DecisionDocument) -> dict[str, Any] | None:
    """Compares the frozen snapshot with the current knowledge state. The snapshot itself never changes after approval."""
    snap = doc.evidence_snapshot
    if not snap or "metrics" not in snap:
        return None
    ini = s.get(m.Initiative, doc.initiative_id)
    assert ini is not None
    u = evidence_universe(s, ini)
    cur: md.ProblemMetrics = u["metrics"]
    sm = snap["metrics"]
    snap_ids = {e["evidence_id"] for e in snap["evidence"]}
    cur_ids = {str(r["id"]) for r in u["rows"]}
    snap_chunks = {e["chunk_id"] for e in snap["evidence"]}
    new_rows = [r for r in u["rows"] if str(r["chunk_id"]) not in snap_chunks]
    return {
        "current": cur.to_dict(),
        "changes": {
            "supporting_customers": cur.supporting_customers - sm["supporting_customers"],
            "supporting_statements": cur.supporting_statements - sm["supporting_statements"],
            "contradicting_statements": cur.contradicting_statements - sm["contradicting_statements"],
            "evidence_added": len(cur_ids - snap_ids) if new_rows else 0,
            "evidence_removed": len(snap_ids - cur_ids),
        },
        "new_evidence": [{"quote": r["quote"], "relation": r["relation"], "customer_name": r["customer_name"], "occurred_at": r["occurred_at"].isoformat()} for r in new_rows[:10]],
        "has_new_knowledge": bool(new_rows) or bool(snap_ids - cur_ids)
        or cur.supporting_customers != sm["supporting_customers"] or cur.contradicting_statements != sm["contradicting_statements"],
        "snapshot_taken_at": snap.get("taken_at"),
    }


# --------------------------------------------------------------------------- workflow
def submit(s: Session, doc: m.DecisionDocument, actor: uuid.UUID) -> None:
    if doc.state != "draft":
        raise conflict("not_draft", "Nur Entwürfe können zur Prüfung eingereicht werden")
    problems = []
    if len(doc.options) < 2:
        problems.append("Mindestens zwei Handlungsoptionen sind nötig.")
    if not doc.recommendation_text.strip():
        problems.append("Die Begründung des Teams fehlt.")
    if doc.snapshot_taken_at is None:
        problems.append("Der Belegstand wurde noch nicht eingefroren (Snapshot aktualisieren).")
    if not doc.evidence_snapshot.get("evidence"):
        problems.append("Es sind keine Belege im Snapshot.")
    if problems:
        raise ApiError(422, "not_ready_for_review", "Die Vorlage ist noch nicht prüfbar", " ".join(problems), problems=problems)
    doc.state, doc.submitted_by = "in_review", actor
    doc.version += 1
    ini = s.get(m.Initiative, doc.initiative_id)
    if ini is not None and ini.status == "proposed":
        ini.status = "evaluating"


def request_changes(doc: m.DecisionDocument) -> None:
    if doc.state != "in_review":
        raise conflict("not_in_review", "Die Vorlage ist nicht in Prüfung")
    doc.state = "draft"
    doc.version += 1


def approve(s: Session, doc: m.DecisionDocument, actor: uuid.UUID, allow_self: bool) -> None:
    if doc.state != "in_review":
        raise conflict("not_in_review", "Nur Vorlagen in Prüfung können freigegeben werden")
    if not allow_self and actor in (doc.created_by, doc.submitted_by):
        raise forbidden("Die Freigabe muss durch eine andere Person als die Autorin oder den Autor erfolgen (Vier-Augen-Prinzip).")
    # supersede the previously approved revision first (only one approved revision per initiative)
    s.execute(update(m.DecisionDocument).where(m.DecisionDocument.initiative_id == doc.initiative_id, m.DecisionDocument.state == "approved",
                                               m.DecisionDocument.id != doc.id).values(state="superseded"))
    doc.state, doc.approved_by, doc.approved_at = "approved", actor, now()
    doc.version += 1
    ini = s.get(m.Initiative, doc.initiative_id)
    if ini is not None:
        ini.status = "decided"


def count_open(s: Session) -> int:
    return s.scalar(select(func.count()).select_from(m.DecisionDocument).where(m.DecisionDocument.state == "in_review")) or 0


__all__ = ["datetime"]
