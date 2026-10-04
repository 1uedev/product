"""AI rationale draft for a decision document. Works only from the frozen snapshot; output is re-verified."""

from __future__ import annotations

import time
import uuid

from sqlalchemy import select, update

from decision_evidence.ai.factory import estimate_cost, get_provider
from decision_evidence.ai.schemas import ChunkInput, ProviderError, RationaleRequest
from decision_evidence.ai.verification import verify_rationale
from decision_evidence.config import get_settings
from decision_evidence.db import models as m
from decision_evidence.jobs.runner import JobContext, JobError, job_handler
from decision_evidence.modules.analysis.jobs import wait_mock_delay
from decision_evidence.modules.analysis.service import ai_calls_this_month
from decision_evidence.modules.common import tenant_settings
from decision_evidence.tenancy.context import add_audit


@job_handler("draft_rationale")
def draft_rationale(ctx: JobContext) -> None:
    settings = get_settings()
    doc_id = uuid.UUID(ctx.payload["document_id"])
    with ctx.session() as s:
        if ai_calls_this_month(s) >= tenant_settings(s).ai_monthly_call_budget:
            raise JobError("ai_budget_exhausted", "Das KI-Aufrufbudget dieses Monats ist aufgebraucht.")
        doc = s.get(m.DecisionDocument, doc_id)
        if doc is None or doc.state != "draft":
            raise JobError("document_not_draft", "Die Entscheidungsvorlage ist nicht mehr im Entwurf.")
        snap, sc = doc.evidence_snapshot, doc.scoring_snapshot
        # only chunks that still exist for this tenant (RLS) AND are part of the snapshot are allowed as context
        snap_ids = [uuid.UUID(e["chunk_id"]) for e in snap.get("evidence", [])]
        live = {r.id: r.text for r in s.execute(select(m.SourceChunk.id, m.SourceChunk.text).where(m.SourceChunk.id.in_(snap_ids)))}
        relation = {uuid.UUID(e["chunk_id"]): e["relation"] for e in snap.get("evidence", [])}
        evidence = [ChunkInput(cid, text, relation=relation[cid]) for cid, text in live.items()][: tenant_settings(s).max_ai_context_chunks]
        results = snap.get("result_ids", {})
        request = RationaleRequest(problem_title=snap["problem"]["title"], problem_description=snap["problem"].get("description", ""), evidence=evidence, results=results,
                                   options=[{k: o.get(k) for k in ("key", "name", "description", "expected_effects", "risks")} for o in doc.options],
                                   scores=[{"name": r["name"], "total": r["total"], "missing": r["missing"]} for r in sc.get("results", [])])
        provider = get_provider(settings)
        s.execute(update(m.AiRun).where(m.AiRun.job_id == ctx.job_id, m.AiRun.status == "running").values(status="failed", error_code="abandoned"))
        run = m.AiRun(job_id=ctx.job_id, purpose="rationale", provider=provider.name, model=provider.model, prompt_version="rationale-v1",
                      input_hash=str(ctx.payload.get("version", "")), status="running")
        s.add(run)
        s.flush()
        run_id = run.id
        allowed_chunks, allowed_results = {e.id for e in evidence}, set(results)
    ctx.set_progress(30)
    started = time.perf_counter()
    try:
        if provider.name == "mock" and settings.mock_ai_delay_seconds > 0:
            wait_mock_delay(ctx, settings.mock_ai_delay_seconds)
        result = provider.draft_rationale(request)
    except ProviderError as exc:
        with ctx.session() as s:
            s.execute(update(m.AiRun).where(m.AiRun.id == run_id).values(status="failed", error_code=exc.code, duration_ms=int((time.perf_counter() - started) * 1000)))
        raise JobError(exc.code, exc.message, retryable=exc.retryable) from exc
    ctx.check_lease()
    draft, report = verify_rationale(result.output, allowed_chunks, allowed_results)  # type: ignore[arg-type]
    draft["provider_label"] = "Demo-Entwurf (regelbasiert, kein Sprachmodell)" if provider.name == "mock" else f"KI-Entwurf ({provider.model})"
    draft["verification"] = report
    cost = estimate_cost(settings, result.input_tokens, result.output_tokens)
    with ctx.session() as s:
        doc = s.scalar(select(m.DecisionDocument).where(m.DecisionDocument.id == doc_id).with_for_update())
        if doc is None or doc.state != "draft":
            raise JobError("document_not_draft", "Die Entscheidungsvorlage ist nicht mehr im Entwurf.")
        # the draft lives next to the human text; it does not bump the document version (no edit conflicts for the author)
        s.execute(update(m.DecisionDocument).where(m.DecisionDocument.id == doc_id).values(ai_draft=draft, ai_run_id=run_id))
        run = s.get(m.AiRun, run_id)
        assert run is not None
        run.status, run.input_tokens, run.output_tokens, run.duration_ms = "succeeded", result.input_tokens, result.output_tokens, result.duration_ms
        run.verification = report
        if cost:
            run.estimated_cost, run.cost_currency, run.price_basis = cost
        add_audit(s, ctx.requested_by, "decision.ai_draft_stored", "decision_document", doc_id, {"claims": report["accepted_claims"], "rejected": report["rejected_claims"]})
        ctx.complete(s, {"document_id": str(doc_id), "ai_run_id": str(run_id), "claims": report["accepted_claims"], "rejected_claims": report["rejected_claims"],
                         "provider": provider.name, "demo": provider.name == "mock"})
