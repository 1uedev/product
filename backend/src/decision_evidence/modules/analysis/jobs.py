"""Analysis job: load context (short tx) -> provider call (no tx) -> verify -> persist + complete (one tx)."""

from __future__ import annotations

import time
import uuid
from typing import Any

from sqlalchemy import select, update

from decision_evidence.ai.factory import estimate_cost, get_provider
from decision_evidence.ai.schemas import AnalysisRequest, ChunkInput, ProviderError
from decision_evidence.ai.verification import verify_analysis
from decision_evidence.config import get_settings
from decision_evidence.db import models as m
from decision_evidence.jobs.runner import JobContext, JobError, job_handler
from decision_evidence.modules.analysis.service import ai_calls_this_month
from decision_evidence.modules.common import tenant_settings
from decision_evidence.modules.problems.service import log_history
from decision_evidence.tenancy.context import add_audit


def wait_mock_delay(ctx: JobContext, seconds: float) -> None:
    """Test knob: lets integration tests kill the worker while a job is in flight."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        ctx.check_lease()
        time.sleep(0.5)


@job_handler("analyze_feedback")
def analyze_feedback(ctx: JobContext) -> None:
    settings = get_settings()
    chunk_ids = [uuid.UUID(c) for c in ctx.payload["chunk_ids"]]
    with ctx.session() as s:
        ts = tenant_settings(s)
        if ai_calls_this_month(s) >= ts.ai_monthly_call_budget:
            raise JobError("ai_budget_exhausted", "Das KI-Aufrufbudget dieses Monats ist aufgebraucht.")
        # abandon runs of earlier attempts of this job that never finished
        s.execute(update(m.AiRun).where(m.AiRun.job_id == ctx.job_id, m.AiRun.status == "running").values(status="failed", error_code="abandoned"))
        rows = s.execute(select(m.SourceChunk.id, m.SourceChunk.text, m.FeedbackItem.occurred_at, m.CustomerAccount.segment)
                         .join(m.FeedbackItem, m.FeedbackItem.source_record_id == m.SourceChunk.source_record_id)
                         .outerjoin(m.CustomerAccount, m.CustomerAccount.id == m.FeedbackItem.customer_account_id)
                         .where(m.SourceChunk.id.in_(chunk_ids))).all()
        allowed = {r.id: r.text for r in rows}
        chunk_inputs = [ChunkInput(r.id, r.text, r.occurred_at, r.segment) for r in rows]
        provider = get_provider(settings)
        run = m.AiRun(job_id=ctx.job_id, purpose="analysis", provider=provider.name, model=provider.model, prompt_version="analysis-v1",
                      input_hash=ctx.payload.get("input_hash", ""), status="running")
        s.add(run)
        s.flush()
        run_id = run.id
    if not chunk_inputs:
        raise JobError("nothing_to_analyze", "Es gibt keine Belege zum Analysieren.")
    ctx.set_progress(20)

    started = time.perf_counter()
    try:
        if provider.name == "mock" and settings.mock_ai_delay_seconds > 0:
            wait_mock_delay(ctx, settings.mock_ai_delay_seconds)
        result = provider.analyze(AnalysisRequest(chunk_inputs, max_problems=ctx.payload.get("max_problems", 12)))
    except ProviderError as exc:
        _fail_run(ctx, run_id, exc.code, int((time.perf_counter() - started) * 1000))
        raise JobError(exc.code, exc.message, retryable=exc.retryable) from exc
    ctx.check_lease()
    ctx.set_progress(70)

    verified, report = verify_analysis(result.output, allowed)  # type: ignore[arg-type]
    cost = estimate_cost(settings, result.input_tokens, result.output_tokens)
    with ctx.session() as s:
        chunk_to_fi = {c: f for c, f in s.execute(select(m.SourceChunk.id, m.FeedbackItem.id)
                                                   .join(m.FeedbackItem, m.FeedbackItem.source_record_id == m.SourceChunk.source_record_id)
                                                   .where(m.SourceChunk.id.in_(list(allowed))))}
        run = s.get(m.AiRun, run_id)
        assert run is not None
        run.status, run.input_tokens, run.output_tokens, run.duration_ms = "succeeded", result.input_tokens, result.output_tokens, result.duration_ms
        run.verification = report.to_dict()
        if cost:
            run.estimated_cost, run.cost_currency, run.price_basis = cost
        created = 0
        for ordinal, vp in enumerate(verified):
            exists_ = s.scalar(select(m.Problem.id).where(m.Problem.created_by_job_id == ctx.job_id, m.Problem.job_ordinal == ordinal))
            if exists_:  # persistence is idempotent for re-delivered jobs
                continue
            p = m.Problem(title=vp.title, description=vp.description, status="proposed", origin="ai", created_by_job_id=ctx.job_id,
                          job_ordinal=ordinal, ai_run_id=run_id, created_by=ctx.requested_by)
            s.add(p)
            s.flush()
            for ev in vp.evidence:
                s.add(m.ProblemEvidence(problem_id=p.id, feedback_item_id=chunk_to_fi[ev.chunk_id], source_chunk_id=ev.chunk_id, relation=ev.relation,
                                        extracted_quote=ev.quote, origin="ai", ai_run_id=run_id, human_verified=False))
            log_history(s, p.id, "created", ctx.requested_by, origin="ai", ai_run_id=run_id, evidence=len(vp.evidence))
            created += 1
        summary: dict[str, Any] = {"problems_created": created, "evidence_accepted": report.accepted_evidence,
                                   "evidence_rejected": len(report.rejected_evidence), "problems_rejected": report.rejected_problems,
                                   "ai_run_id": str(run_id), "provider": provider.name, "model": provider.model, "demo": provider.name == "mock",
                                   "chunks_analyzed": len(chunk_inputs), "truncated": bool(ctx.payload.get("truncated"))}
        add_audit(s, ctx.requested_by, "analysis.completed", "job", ctx.job_id, {k: v for k, v in summary.items() if not isinstance(v, str)})
        ctx.complete(s, summary)


def _fail_run(ctx: JobContext, run_id: uuid.UUID, code: str, duration_ms: int) -> None:
    with ctx.session() as s:
        s.execute(update(m.AiRun).where(m.AiRun.id == run_id).values(status="failed", error_code=code, duration_ms=duration_ms))
