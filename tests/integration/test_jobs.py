"""Job system guarantees: transactional outbox, at-least-once delivery handled idempotently, leases, retries, recovery."""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import uuid
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import select, text

from conftest import Api, import_demo, run_jobs  # type: ignore[import-not-found]
from decision_evidence.ai.mock import MockProvider
from decision_evidence.ai.ollama_provider import OllamaProvider
from decision_evidence.ai.schemas import ProviderError
from decision_evidence.config import Settings
from decision_evidence.db import models as m
from decision_evidence.db.engines import get_engine
from decision_evidence.db.session import plain_session, tenant_session
from decision_evidence.jobs import publisher, runner
from decision_evidence.jobs.service import enqueue_job

UTC = dt.UTC


def _outbox(tenant_id: uuid.UUID, job_id: uuid.UUID) -> list[dict]:
    with tenant_session(get_engine("app"), tenant_id) as s:
        return [dict(r._mapping) for r in s.execute(text("select * from infra.outbox where job_id = :j order by created_at"), {"j": job_id})]


def _job(tenant_id: uuid.UUID, job_id: uuid.UUID) -> m.Job:
    with tenant_session(get_engine("app"), tenant_id) as s:
        j = s.get(m.Job, job_id)
        s.expunge(j)
        return j


def _start_analysis(workspace) -> tuple[Api, uuid.UUID]:  # type: ignore[no-untyped-def]
    ed: Api = workspace["editor"]
    import_demo(ed)
    r = ed.post("/analysis", {"scope": "unassigned"})
    assert r.status_code == 202
    return ed, uuid.UUID(r.json()["job"]["id"])


def test_job_and_outbox_row_are_written_in_one_transaction(workspace) -> None:  # type: ignore[no-untyped-def]
    tid = workspace["tenant"].id
    ed, job_id = _start_analysis(workspace)
    ob = _outbox(tid, job_id)
    assert len(ob) == 1 and ob[0]["published_at"] is None and ob[0]["event_type"] == "job.enqueued" and ob[0]["schema_version"] == 1
    # a rollback leaves neither job nor outbox row behind
    with pytest.raises(RuntimeError), tenant_session(get_engine("app"), tid) as s:
        j, _ = enqueue_job(s, kind="analyze_feedback", idempotency_key="rolled-back", payload={}, requested_by=None)
        s.flush()
        raise RuntimeError("boom")
    with tenant_session(get_engine("app"), tid) as s:
        assert s.scalar(select(m.Job).where(m.Job.idempotency_key == "rolled-back")) is None


def test_publisher_publishes_ids_only_and_marks_after_confirmation(workspace, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    tid = workspace["tenant"].id
    _ed, job_id = _start_analysis(workspace)
    sent: list[dict] = []
    monkeypatch.setattr(publisher, "get_celery", lambda: SimpleNamespace(send_task=lambda name, args, **kw: sent.append({"name": name, "args": args, **kw})))
    # drain everything due (other tests may have left rows); ours must be among them
    while publisher.publish_one():
        pass
    mine = [s for s in sent if s["args"][0]["job_id"] == str(job_id)]
    assert len(mine) == 1
    envelope = mine[0]["args"][0]
    assert set(envelope) == {"event_id", "type", "schema_version", "tenant_id", "job_id", "occurred_at", "correlation_id"}
    assert envelope["tenant_id"] == str(tid) and envelope["type"] == "job.enqueued"
    assert _outbox(tid, job_id)[0]["published_at"] is not None


def test_broker_outage_keeps_rows_unpublished_with_backoff_then_recovers(workspace, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    tid = workspace["tenant"].id
    _ed, job_id = _start_analysis(workspace)

    def down(*_a, **_k):  # type: ignore[no-untyped-def]
        raise ConnectionError("broker down")

    monkeypatch.setattr(publisher, "get_celery", lambda: SimpleNamespace(send_task=down))
    while publisher.publish_one():
        pass
    ob = _outbox(tid, job_id)[0]
    assert ob["published_at"] is None and ob["attempts"] >= 1 and ob["last_error_code"] == "ConnectionError" and ob["available_at"] > dt.datetime.now(UTC)
    assert _job(tid, job_id).status == "queued"                      # the job is not lost, just waiting
    # broker returns, backoff elapsed
    with plain_session(get_engine("outbox")) as s:
        s.execute(text("update infra.outbox set available_at = now() where job_id = :j"), {"j": job_id})
    sent = []
    monkeypatch.setattr(publisher, "get_celery", lambda: SimpleNamespace(send_task=lambda name, args, **kw: sent.append(args[0]["job_id"])))
    while publisher.publish_one():
        pass
    assert str(job_id) in sent and _outbox(tid, job_id)[0]["published_at"] is not None


def test_duplicate_delivery_of_the_same_job_runs_once(workspace) -> None:  # type: ignore[no-untyped-def]
    tid = workspace["tenant"].id
    ed, job_id = _start_analysis(workspace)
    assert runner.execute_job(tid, job_id) == "succeeded"
    n = len(ed.get("/problems").json()["items"])
    assert runner.execute_job(tid, job_id) == "skipped"              # at-least-once delivery, idempotent processing
    assert len(ed.get("/problems").json()["items"]) == n
    with tenant_session(get_engine("app"), tid) as s:
        assert s.scalar(text("select count(*) from app.ai_runs where job_id = :j"), {"j": job_id}) == 1
    # a job addressed with the wrong tenant is unknown (RLS), not an error and never touched
    assert runner.execute_job(workspace["other"].id, job_id) == "skipped"


def test_job_cannot_be_claimed_while_leased_but_can_after_expiry(workspace) -> None:  # type: ignore[no-untyped-def]
    tid = workspace["tenant"].id
    _ed, job_id = _start_analysis(workspace)
    ctx = runner.claim_job(tid, job_id)
    assert ctx is not None and ctx.attempt == 1
    assert runner.claim_job(tid, job_id) is None                      # leased by another worker
    with tenant_session(get_engine("worker"), tid) as s:
        s.execute(text("update app.jobs set lease_expires_at = now() - interval '1 second' where id = :j"), {"j": job_id})
    again = runner.claim_job(tid, job_id)
    assert again is not None and again.attempt == 2 and again.claim_token != ctx.claim_token


def test_worker_that_lost_its_lease_cannot_write_results(workspace, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    tid = workspace["tenant"].id
    ed, job_id = _start_analysis(workspace)
    real = MockProvider()

    class Stealing:
        name, model = real.name, real.model

        def analyze(self, req):  # type: ignore[no-untyped-def]
            with tenant_session(get_engine("worker"), tid) as s:      # another worker re-claims the job meanwhile
                s.execute(text("update app.jobs set claim_token = gen_random_uuid() where id = :j"), {"j": job_id})
            return real.analyze(req)

    monkeypatch.setattr("decision_evidence.modules.analysis.jobs.get_provider", lambda _s: Stealing())
    assert runner.execute_job(tid, job_id) == "lease_lost"
    assert ed.get("/problems").json()["total"] == 0                    # nothing persisted by the stale worker
    assert _job(tid, job_id).status == "running"


def test_provider_timeout_is_retried_with_backoff_then_fails_visibly_and_can_be_retried(workspace, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    tid = workspace["tenant"].id
    ed, job_id = _start_analysis(workspace)

    class Timeout:
        name, model = "anthropic", "claude-sonnet-5-5"

        def analyze(self, req):  # type: ignore[no-untyped-def]
            raise ProviderError("ai_timeout", "Der KI-Dienst hat nicht rechtzeitig geantwortet.", retryable=True)

    monkeypatch.setattr("decision_evidence.modules.analysis.jobs.get_provider", lambda _s: Timeout())
    assert runner.execute_job(tid, job_id) == "retry"
    j = _job(tid, job_id)
    assert j.status == "queued" and j.attempts == 1 and j.error_code == "ai_timeout"
    ob = _outbox(tid, job_id)
    assert len(ob) == 2 and ob[1]["available_at"] > dt.datetime.now(UTC)    # re-published later (backoff)
    assert runner.execute_job(tid, job_id) == "retry"
    assert runner.execute_job(tid, job_id) == "failed"                       # limit reached: visible failure, no silent switch to demo
    j = _job(tid, job_id)
    assert j.status == "failed" and j.error_code == "ai_timeout" and j.attempts == 3
    assert ed.get("/problems").json()["total"] == 0
    with tenant_session(get_engine("app"), tid) as s:
        assert {r.provider for r in s.scalars(select(m.AiRun).where(m.AiRun.job_id == job_id))} == {"anthropic"}
        assert {r.status for r in s.scalars(select(m.AiRun).where(m.AiRun.job_id == job_id))} == {"failed"}
    # manual retry from the UI resets the job; with a working provider it now succeeds, exactly once
    r = ed.post(f"/jobs/{job_id}/retry")
    assert r.status_code == 202 and r.json()["status"] == "queued"
    monkeypatch.undo()
    assert runner.execute_job(tid, job_id) == "succeeded"
    assert ed.get("/problems").json()["total"] >= 2


def test_non_retryable_errors_fail_immediately(workspace, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    tid = workspace["tenant"].id
    _ed, job_id = _start_analysis(workspace)

    class Refuse:
        name, model = "anthropic", "m"

        def analyze(self, req):  # type: ignore[no-untyped-def]
            raise ProviderError("ai_auth_failed", "Der KI-Schlüssel wurde abgelehnt.")

    monkeypatch.setattr("decision_evidence.modules.analysis.jobs.get_provider", lambda _s: Refuse())
    assert runner.execute_job(tid, job_id) == "failed"
    assert _job(tid, job_id).attempts == 1


def test_orphaned_jobs_are_requeued_or_failed_by_recovery(workspace) -> None:  # type: ignore[no-untyped-def]
    tid = workspace["tenant"].id
    ed, job_id = _start_analysis(workspace)
    ctx = runner.claim_job(tid, job_id)                       # a worker takes the job ... and dies
    assert ctx is not None
    with tenant_session(get_engine("worker"), tid) as s:
        s.execute(text("update app.jobs set lease_expires_at = now() - interval '1 minute' where id = :j"), {"j": job_id})
    assert runner.recover_stale_jobs() >= 1
    j = _job(tid, job_id)
    assert j.status == "queued" and j.claim_token is None and len(_outbox(tid, job_id)) == 2
    assert runner.execute_job(tid, job_id) == "succeeded"
    assert ed.get("/problems").json()["total"] >= 2           # processed exactly once overall
    # attempts exhausted -> failed with a clear code instead of an endless loop
    ed2, job2 = _start_analysis_again(workspace)
    with tenant_session(get_engine("worker"), tid) as s:
        s.execute(text("update app.jobs set status='running', attempts = max_attempts, claim_token = gen_random_uuid(), lease_expires_at = now() - interval '1 minute' where id = :j"), {"j": job2})
    runner.recover_stale_jobs()
    j2 = _job(tid, job2)
    assert j2.status == "failed" and j2.error_code == "lease_expired"
    del ed2


def _start_analysis_again(workspace) -> tuple[Api, uuid.UUID]:  # type: ignore[no-untyped-def]
    ed: Api = workspace["editor"]
    chunk = ed.post("/imports/text", {"title": "Neu", "text": "Die Rechnung kommt zu spät und die Rechnung ist falsch.\n\nNoch eine Rechnung mit falscher Adresse."})
    ed.post(f"/imports/{chunk.json()['id']}/commit")
    run_jobs(workspace["tenant"].id)
    r = ed.post("/analysis", {"scope": "unassigned"})
    assert r.status_code == 202, r.text
    return ed, uuid.UUID(r.json()["job"]["id"])


def test_worker_restart_mid_job_leads_to_one_result(workspace) -> None:  # type: ignore[no-untyped-def]
    """Simulates a worker crash after claiming: the lease expires, recovery requeues, a second worker finishes."""
    tid = workspace["tenant"].id
    ed, job_id = _start_analysis(workspace)
    assert runner.claim_job(tid, job_id) is not None            # worker 1 claims and crashes
    assert runner.execute_job(tid, job_id) == "skipped"         # worker 2 cannot steal a live lease
    with tenant_session(get_engine("worker"), tid) as s:
        s.execute(text("update app.jobs set lease_expires_at = now() - interval '1 second' where id = :j"), {"j": job_id})
    assert runner.execute_job(tid, job_id) == "succeeded"       # lease expired: the redelivered message is claimed
    assert ed.get("/problems").json()["total"] >= 2
    assert runner.execute_job(tid, job_id) == "skipped"
    with tenant_session(get_engine("app"), tid) as s:
        titles = list(s.scalars(select(m.Problem.title)))
        assert len(titles) == len(set(titles)), "no duplicate technical results"


def test_stale_pending_files_are_cleaned_up(workspace, memory_storage) -> None:  # type: ignore[no-untyped-def]
    tid = workspace["tenant"].id
    with tenant_session(get_engine("app"), tid) as s:
        f = m.FileRecord(object_key=f"tenant/{tid}/orphan1", original_name="o.csv", media_type="text/csv", byte_size=3, sha256="0" * 64, purpose="import_csv", status="pending")
        s.add(f)
        s.flush()
        fid = f.id
    memory_storage.put(f"tenant/{tid}/orphan1", b"abc", "text/csv")
    assert runner.cleanup_stale_files() == 0                                   # too young: an upload may still be in flight
    with tenant_session(get_engine("worker"), tid) as s:
        s.execute(text("update app.files set created_at = now() - interval '2 hours' where id = :i"), {"i": fid})
    assert runner.cleanup_stale_files() >= 1
    assert not memory_storage.exists(f"tenant/{tid}/orphan1")
    with tenant_session(get_engine("app"), tid) as s:
        assert s.get(m.FileRecord, fid).status == "failed"


def test_import_commit_is_all_or_nothing_when_the_worker_fails_midway(workspace, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    tid = workspace["tenant"].id
    ed: Api = workspace["editor"]
    b = ed.upload("customers", "k.csv", "id,name\nC1,A\nC2,B\nC3,C\n").json()
    ed.client.put(ed.url(f"/imports/{b['id']}/settings"), json={}, headers=ed._h())
    ed.post(f"/imports/{b['id']}/commit")
    from decision_evidence.modules.imports import service

    real = service.apply_plan

    def exploding(s, batch, file, plan, progress=lambda p: None):  # type: ignore[no-untyped-def]
        real(s, batch, file, plan, progress)
        raise RuntimeError("crash after inserting rows")

    monkeypatch.setattr(service, "apply_plan", exploding)
    assert run_jobs(tid) == [("import_commit", "retry")]
    assert ed.get("/customers").json()["total"] == 0                          # transaction rolled back, no partial import
    monkeypatch.undo()
    assert runner.execute_job(tid, uuid.UUID(ed.get(f"/imports/{b['id']}").json()["job_id"])) == "succeeded"
    assert ed.get("/customers").json()["total"] == 3
    assert ed.get(f"/imports/{b['id']}").json()["status"] == "committed"


# ----------------------------------------------------------------------------- local model (Ollama)
def _ollama(handler) -> OllamaProvider:  # type: ignore[no-untyped-def]
    client = httpx.Client(base_url="http://ollama.test:11434", transport=httpx.MockTransport(handler))
    return OllamaProvider(Settings(ai_provider="ollama", ollama_model="qwen3:8b", ollama_base_url="http://ollama.test:11434"), client=client)


def _answer_from_prompt(request: httpx.Request) -> httpx.Response:
    """Plays the model: reads the chunks from the prompt, groups the first ones and quotes them. One quote is invented."""
    user = json.loads(request.content)["messages"][1]["content"]
    found = re.findall(r'<chunk id="([0-9a-f-]{36})">(.*?)</chunk>', user, flags=re.S)
    assert found, "the prompt must contain the data chunks"
    first = [(cid, html.unescape(text_)) for cid, text_ in found[:2]]
    evidence = [{"source_chunk_id": cid, "quote": t.strip()[:60], "relation": "supports"} for cid, t in first]
    evidence.append({"source_chunk_id": first[0][0], "quote": "dieses Zitat steht nirgends im Text", "relation": "supports"})
    body = {"problems": [{"title": "Lokal vorgeschlagenes Problem", "description": "von einem lokalen Modell", "evidence": evidence}]}
    return httpx.Response(200, json={"message": {"role": "assistant", "content": json.dumps(body)}, "done": True, "done_reason": "stop",
                                     "prompt_eval_count": 700, "eval_count": 120})


def test_analysis_with_a_local_model_stores_only_verified_proposals(workspace, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    tid = workspace["tenant"].id
    ed, job_id = _start_analysis(workspace)
    monkeypatch.setattr("decision_evidence.modules.analysis.jobs.get_provider", lambda _s: _ollama(_answer_from_prompt))
    assert runner.execute_job(tid, job_id) == "succeeded"
    problems = ed.get("/problems").json()["items"]
    assert [p["title"] for p in problems] == ["Lokal vorgeschlagenes Problem"]
    detail = ed.get(f"/problems/{problems[0]['id']}").json()
    assert len(detail["evidence"]) == 2                       # the invented quote was dropped by the server side verification
    with tenant_session(get_engine("app"), tid) as s:
        run = s.scalars(select(m.AiRun).where(m.AiRun.job_id == job_id)).one()
        assert (run.provider, run.model, run.status) == ("ollama", "qwen3:8b", "succeeded")
        assert run.input_tokens == 700 and run.output_tokens == 120 and run.estimated_cost is None
        assert run.verification["rejected_evidence"]


def test_missing_local_model_fails_visibly_without_retries_and_without_demo_fallback(workspace, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    tid = workspace["tenant"].id
    ed, job_id = _start_analysis(workspace)
    monkeypatch.setattr("decision_evidence.modules.analysis.jobs.get_provider",
                        lambda _s: _ollama(lambda r: httpx.Response(404, json={"error": "model 'qwen3:8b' not found"})))
    assert runner.execute_job(tid, job_id) == "failed"
    j = _job(tid, job_id)
    assert j.status == "failed" and j.error_code == "ai_model_missing" and j.attempts == 1 and "ollama pull" in (j.error_message or "")
    assert ed.get("/problems").json()["total"] == 0
    with tenant_session(get_engine("app"), tid) as s:
        assert {(r.provider, r.status) for r in s.scalars(select(m.AiRun).where(m.AiRun.job_id == job_id))} == {("ollama", "failed")}
