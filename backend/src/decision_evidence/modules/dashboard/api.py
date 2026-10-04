from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

from fastapi import APIRouter
from sqlalchemy import func, select

from decision_evidence.db import models as m
from decision_evidence.modules.common import tenant_settings
from decision_evidence.modules.metrics import domain as md
from decision_evidence.modules.metrics import service as metrics_service
from decision_evidence.tenancy.context import TenantContext, viewer

router = APIRouter(prefix="/api/v1/workspaces/{tenant_id}", tags=["dashboard"])


@router.get("/dashboard")
def dashboard(ctx: Annotated[TenantContext, viewer]) -> dict[str, Any]:
    """Overview figures, all computed from stored data. Money is shown per kind and per currency, never summed across them."""
    with ctx.session() as s:
        customers, opps = metrics_service.load_universe(s)
        arr, sales, unknown = md.money_for_customers(customers.values())
        n_fb = s.scalar(select(func.count()).select_from(m.FeedbackItem)) or 0
        n_src = s.scalar(select(func.count()).select_from(m.SourceRecord)) or 0
        with_fb = s.scalar(select(func.count(func.distinct(m.FeedbackItem.customer_account_id)))) or 0
        unassigned = s.scalar(select(func.count()).select_from(m.FeedbackItem).where(~select(m.ProblemEvidence.id).where(
            m.ProblemEvidence.feedback_item_id == m.FeedbackItem.id).exists())) or 0
        by_status = dict(s.execute(select(m.Problem.status, func.count()).group_by(m.Problem.status)).all())
        init_by_status = dict(s.execute(select(m.Initiative.status, func.count()).group_by(m.Initiative.status)).all())
        dec_by_state = dict(s.execute(select(m.DecisionDocument.state, func.count()).group_by(m.DecisionDocument.state)).all())
        stats = metrics_service.list_stats(s, list(s.scalars(select(m.Problem.id).where(m.Problem.status != "archived"))))
        titles = dict(s.execute(select(m.Problem.id, m.Problem.title)).all())
        top = sorted(stats.items(), key=lambda kv: (-kv[1]["customers"], -kv[1]["statements"], str(kv[0])))[:5]
        seg = func.coalesce(m.CustomerAccount.segment, "unbekannt")
        seg_counts = dict(s.execute(select(seg, func.count()).group_by(seg)).all())
        recent_jobs = [{"id": str(j.id), "kind": j.kind, "status": j.status, "created_at": j.created_at.isoformat(), "error_code": j.error_code}
                       for j in s.scalars(select(m.Job).order_by(m.Job.created_at.desc()).limit(5))]
        in_review = [{"id": str(d.id), "title": d.title, "revision": d.revision} for d in s.scalars(select(m.DecisionDocument).where(m.DecisionDocument.state == "in_review").limit(5))]
        ts = tenant_settings(s)
        fresh_cut = datetime.now(UTC) - timedelta(days=ts.evidence_fresh_days)
        old_fb = s.scalar(select(func.count()).select_from(m.FeedbackItem).where(m.FeedbackItem.occurred_at < fresh_cut)) or 0
        origins = dict(s.execute(select(m.SourceRecord.origin, func.count()).group_by(m.SourceRecord.origin)).all())
        open_pipe = md.money_for_opportunities(opps.values(), "open")
        lost = md.money_for_opportunities(opps.values(), "lost")
        return {
            "counts": {"customers": len(customers), "opportunities": len(opps), "feedback_items": n_fb, "sources": n_src,
                       "problems": {k: by_status.get(k, 0) for k in ("proposed", "confirmed", "archived")},
                       "initiatives": init_by_status, "decisions": dec_by_state},
            "coverage": {"customers_with_feedback": with_fb, "customers_total": len(customers), "unassigned_feedback": unassigned,
                         "feedback_older_than_fresh_days": old_fb, "fresh_days": ts.evidence_fresh_days},
            "money": {"arr": arr.as_dict(), "annual_sales": sales.as_dict(), "open_pipeline": open_pipe.as_dict(), "lost_volume": lost.as_dict(),
                      "customers_without_value": unknown,
                      "note": "Beträge werden je Art und Währung getrennt ausgewiesen und nicht addiert oder umgerechnet; unbekannte Werte sind nicht als 0 gerechnet."},
            "segments": seg_counts,
            "top_problems": [{"id": str(pid), "title": titles.get(pid, "?"), "customers": st["customers"], "statements": st["statements"], "contradictions": st["contradictions"]}
                             for pid, st in top],
            "recent_jobs": recent_jobs, "decisions_in_review": in_review, "source_origins": origins,
        }
