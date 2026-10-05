"""Loads the tenant universe from the database and feeds the pure metrics domain."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from decision_evidence.db import models as m
from decision_evidence.modules.metrics import domain as d


def load_universe(s: Session) -> tuple[dict[uuid.UUID, d.CustomerRef], dict[uuid.UUID, d.OpportunityRef]]:
    customers = {
        c.id: d.CustomerRef(c.id, c.name, c.segment, c.commercial_value, c.value_basis, c.currency)
        for c in s.scalars(select(m.CustomerAccount))
    }
    opps = {
        o.id: d.OpportunityRef(o.id, o.customer_account_id, o.stage, o.amount, o.currency)
        for o in s.scalars(select(m.Opportunity))
    }
    return customers, opps


def evidence_rows(s: Session, problem_id: uuid.UUID) -> list[dict[str, Any]]:
    """Evidence with everything the UI and the snapshots need, in a stable order."""
    q = (
        select(
            m.ProblemEvidence, m.SourceChunk, m.SourceRecord, m.FeedbackItem,
            m.CustomerAccount.name.label("customer_name"), m.CustomerAccount.segment.label("customer_segment"),
            m.Opportunity.name.label("opportunity_name"), m.Opportunity.stage.label("opportunity_stage"),
            m.FileRecord.original_name.label("file_name"),
        )
        .join(m.SourceChunk, m.SourceChunk.id == m.ProblemEvidence.source_chunk_id)
        .join(m.SourceRecord, m.SourceRecord.id == m.SourceChunk.source_record_id)
        .join(m.FeedbackItem, m.FeedbackItem.id == m.ProblemEvidence.feedback_item_id)
        .outerjoin(m.CustomerAccount, m.CustomerAccount.id == m.FeedbackItem.customer_account_id)
        .outerjoin(m.Opportunity, m.Opportunity.id == m.FeedbackItem.opportunity_id)
        .outerjoin(m.FileRecord, m.FileRecord.id == m.SourceRecord.file_id)
        .where(m.ProblemEvidence.problem_id == problem_id)
        .order_by(m.ProblemEvidence.relation, m.FeedbackItem.occurred_at.desc(), m.ProblemEvidence.id)
    )
    out = []
    for pe, sc, sr, fi, cname, cseg, oname, ostage, fname in s.execute(q).all():
        out.append({
            "id": pe.id, "problem_id": pe.problem_id, "relation": pe.relation, "quote": pe.extracted_quote, "origin": pe.origin,
            "human_verified": pe.human_verified, "chunk_id": sc.id, "chunk_text": sc.text, "locator": sc.locator,
            "source_record_id": sr.id, "source_title": sr.title, "source_origin": sr.origin, "source_kind": sr.source_kind,
            "file_id": sr.file_id, "file_name": fname, "feedback_id": fi.id, "channel": fi.channel, "occurred_at": fi.occurred_at,
            "customer_id": fi.customer_account_id, "customer_name": cname, "customer_segment": cseg,
            "opportunity_id": fi.opportunity_id, "opportunity_name": oname, "opportunity_stage": ostage,
        })
    return out


def to_refs(rows: list[dict[str, Any]]) -> list[d.EvidenceRef]:
    return [d.EvidenceRef(r["id"], r["chunk_id"], r["feedback_id"], r["customer_id"], r["opportunity_id"], r["relation"],
                          r["occurred_at"], r["human_verified"]) for r in rows]


def problem_metrics(s: Session, problem_id: uuid.UUID, fresh_days: int, now: datetime | None = None,
                    universe: tuple[dict, dict] | None = None) -> tuple[d.ProblemMetrics, list[dict[str, Any]]]:
    customers, opps = universe or load_universe(s)
    rows = evidence_rows(s, problem_id)
    metrics = d.compute_problem_metrics(to_refs(rows), customers, opps, now=now, fresh_days=fresh_days)
    anywhere = s.scalar(select(func.count(func.distinct(m.FeedbackItem.customer_account_id))))
    metrics.coverage["customers_with_any_feedback"] = anywhere or 0
    return metrics, rows


def list_stats(s: Session, problem_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
    """Cheap aggregates for the problem list (unique customers vs. statements, last evidence date)."""
    if not problem_ids:
        return {}
    q = (
        select(
            m.ProblemEvidence.problem_id,
            func.count().filter(m.ProblemEvidence.relation == "supports").label("statements"),
            func.count(func.distinct(m.FeedbackItem.customer_account_id)).filter(m.ProblemEvidence.relation == "supports").label("customers"),
            func.count().filter(m.ProblemEvidence.relation == "contradicts").label("contradictions"),
            func.count().filter(m.ProblemEvidence.human_verified).label("verified"),
            func.max(m.FeedbackItem.occurred_at).filter(m.ProblemEvidence.relation == "supports").label("latest"),
        )
        .join(m.FeedbackItem, m.FeedbackItem.id == m.ProblemEvidence.feedback_item_id)
        .where(m.ProblemEvidence.problem_id.in_(problem_ids))
        .group_by(m.ProblemEvidence.problem_id)
    )
    return {r.problem_id: {"statements": r.statements, "customers": r.customers, "contradictions": r.contradictions,
                           "verified": r.verified, "latest": r.latest} for r in s.execute(q)}
