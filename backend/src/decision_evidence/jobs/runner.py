"""Worker side: claim, heartbeat, run, retry and recovery.

Rules (see ADR 0003):
* The claim is a short transaction (UPDATE ... RETURNING). A duplicate delivery of a job that is finished or leased by
  another worker is a no-op.
* No database transaction is open while a handler waits for an AI provider. Handlers load inputs in a short
  transaction, compute, then persist results *and* mark the job succeeded in one transaction guarded by claim_token.
* A worker that lost its lease can therefore never write results.
"""

from __future__ import annotations

import threading
import traceback
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.orm import Session

from decision_evidence.config import get_settings
from decision_evidence.db import models as m
from decision_evidence.db.context import current_request_id
from decision_evidence.db.engines import get_engine
from decision_evidence.db.session import plain_session, tenant_session
from decision_evidence.jobs.service import add_outbox
from decision_evidence.observability.logging import get_logger, set_job_id

log = get_logger("decision_evidence.worker")


class JobError(Exception):
    """A failure with a stable, non-sensitive error code. ``retryable`` controls backoff retries."""

    def __init__(self, code: str, message: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


class LeaseLost(Exception):
    pass


@dataclass
class JobContext:
    tenant_id: uuid.UUID
    job_id: uuid.UUID
    claim_token: uuid.UUID
    kind: str
    payload: dict[str, Any]
    requested_by: uuid.UUID | None
    attempt: int
    _lease_lost: threading.Event = field(default_factory=threading.Event)

    def session(self) -> Any:
        return tenant_session(get_engine("worker"), self.tenant_id)

    def check_lease(self) -> None:
        if self._lease_lost.is_set():
            raise LeaseLost()

    def set_progress(self, percent: int) -> None:
        with self.session() as s:
            s.execute(update(m.Job).where(m.Job.id == self.job_id, m.Job.claim_token == self.claim_token)
                      .values(progress=max(0, min(100, percent))))

    def complete(self, s: Session, result: dict[str, Any]) -> None:
        """Mark the job succeeded inside the caller's persistence transaction. Raises LeaseLost if not our claim."""
        res = s.execute(
            update(m.Job).where(m.Job.id == self.job_id, m.Job.claim_token == self.claim_token, m.Job.status == "running")
            .values(status="succeeded", progress=100, result=result, finished_at=datetime.now(UTC), lease_expires_at=None,
                    claim_token=None, error_code=None, error_message=None))
        if res.rowcount != 1:
            raise LeaseLost()


Handler = Callable[[JobContext], None]
HANDLERS: dict[str, Handler] = {}


def job_handler(kind: str) -> Callable[[Handler], Handler]:
    def register(fn: Handler) -> Handler:
        HANDLERS[kind] = fn
        return fn
    return register


# ------------------------------------------------------------------ claim / heartbeat
def claim_job(tenant_id: uuid.UUID, job_id: uuid.UUID) -> JobContext | None:
    s_ = get_settings()
    token = uuid.uuid4()
    with tenant_session(get_engine("worker"), tenant_id) as s:
        row = s.execute(
            update(m.Job)
            .where(m.Job.id == job_id,
                   (m.Job.status == "queued") | ((m.Job.status == "running") & (m.Job.lease_expires_at < datetime.now(UTC))))
            .values(status="running", attempts=m.Job.attempts + 1, claim_token=token, progress=0,
                    lease_expires_at=datetime.now(UTC) + timedelta(seconds=s_.job_lease_seconds),
                    started_at=datetime.now(UTC), error_code=None, error_message=None)
            .returning(m.Job.kind, m.Job.payload, m.Job.requested_by, m.Job.attempts, m.Job.correlation_id)
        ).first()
        if row is None:
            return None
        if row.correlation_id:
            current_request_id.set(row.correlation_id)
        return JobContext(tenant_id, job_id, token, row.kind, dict(row.payload), row.requested_by, row.attempts)


class Heartbeat(threading.Thread):
    def __init__(self, ctx: JobContext) -> None:
        super().__init__(daemon=True, name=f"heartbeat-{ctx.job_id}")
        self.ctx = ctx
        self._stop_evt = threading.Event()

    def run(self) -> None:
        s_ = get_settings()
        while not self._stop_evt.wait(s_.job_heartbeat_seconds):
            try:
                with self.ctx.session() as s:
                    res = s.execute(
                        update(m.Job).where(m.Job.id == self.ctx.job_id, m.Job.claim_token == self.ctx.claim_token,
                                            m.Job.status == "running")
                        .values(lease_expires_at=datetime.now(UTC) + timedelta(seconds=s_.job_lease_seconds)))
                    if res.rowcount != 1:
                        self.ctx._lease_lost.set()
                        return
            except Exception:  # noqa: BLE001
                log.exception("heartbeat failed")

    def stop(self) -> None:
        self._stop_evt.set()


# ------------------------------------------------------------------ finish helpers
def _backoff_seconds(attempt: int) -> int:
    return get_settings().job_backoff_base_seconds * (2 ** max(0, attempt - 1))


def _fail_or_retry(ctx: JobContext, error: JobError) -> str:
    with ctx.session() as s:
        job = s.scalar(select(m.Job).where(m.Job.id == ctx.job_id, m.Job.claim_token == ctx.claim_token).with_for_update())
        if job is None:
            return "lease_lost"
        message = error.message[:500]
        if error.retryable and job.attempts < job.max_attempts:
            job.status, job.lease_expires_at, job.claim_token = "queued", None, None
            job.error_code, job.error_message = error.code, message
            add_outbox(s, job.id, datetime.now(UTC) + timedelta(seconds=_backoff_seconds(job.attempts)))
            return "retry"
        job.status, job.error_code, job.error_message = "failed", error.code, message
        job.finished_at, job.lease_expires_at, job.claim_token = datetime.now(UTC), None, None
        return "failed"


def execute_job(tenant_id: uuid.UUID, job_id: uuid.UUID) -> str:
    """Runs one delivery of a job. Returns the outcome label (also used by tests)."""
    set_job_id(str(job_id))
    try:
        ctx = claim_job(tenant_id, job_id)
        if ctx is None:
            log.info("job not claimable (finished, leased elsewhere or unknown tenant)")
            return "skipped"
        handler = HANDLERS.get(ctx.kind)
        beat = Heartbeat(ctx)
        beat.start()
        try:
            if handler is None:
                raise JobError("unknown_job_kind", f"no handler for {ctx.kind}")
            handler(ctx)
            ctx.check_lease()
            log.info("job succeeded", extra={"kind": ctx.kind, "attempt": ctx.attempt})
            return "succeeded"
        except LeaseLost:
            log.warning("lease lost, discarding result")
            return "lease_lost"
        except JobError as exc:
            outcome = _fail_or_retry(ctx, exc)
            log.warning("job error", extra={"code": exc.code, "outcome": outcome})
            return outcome
        except Exception as exc:  # noqa: BLE001
            log.error("unexpected job failure: %s", traceback.format_exc())
            return _fail_or_retry(ctx, JobError("internal_error", f"Unerwarteter Fehler ({type(exc).__name__})", retryable=True))
        finally:
            beat.stop()
    finally:
        set_job_id(None)


# ------------------------------------------------------------------ recovery
def recover_stale_jobs(limit: int = 50) -> int:
    """Find orphaned jobs through the SECURITY DEFINER function and requeue or fail them, one tenant at a time."""
    with plain_session(get_engine("worker")) as s:
        rows = s.execute(text("SELECT tenant_id, job_id FROM infra.find_stale_jobs(:n)"), {"n": limit}).all()
    recovered = 0
    for tenant_id, job_id in rows:
        with tenant_session(get_engine("worker"), tenant_id) as s:
            job = s.scalar(select(m.Job).where(m.Job.id == job_id).with_for_update(skip_locked=True))
            if job is None:
                continue
            now = datetime.now(UTC)
            if job.status == "running" and job.lease_expires_at and job.lease_expires_at < now:
                if job.attempts >= job.max_attempts:
                    job.status, job.error_code = "failed", "lease_expired"
                    job.error_message = "Die Verarbeitung wurde unterbrochen und das Wiederholungslimit ist erreicht."
                    job.finished_at, job.lease_expires_at, job.claim_token = now, None, None
                else:
                    job.status, job.lease_expires_at, job.claim_token = "queued", None, None
                    add_outbox(s, job.id)
                recovered += 1
                log.warning("recovered orphaned job", extra={"job_id": str(job.id), "new_status": job.status})
            elif job.status == "queued":
                add_outbox(s, job.id)  # message may have been lost in the broker: publish again (harmless duplicate)
                job.updated_at = now
                recovered += 1
    return recovered


def cleanup_stale_files(limit: int = 50) -> int:
    from decision_evidence.storage.s3 import get_storage

    storage = get_storage()
    with plain_session(get_engine("worker")) as s:
        rows = s.execute(text("SELECT tenant_id, file_id FROM infra.find_stale_files(:n)"), {"n": limit}).all()
    cleaned = 0
    for tenant_id, file_id in rows:
        with tenant_session(get_engine("worker"), tenant_id) as s:
            f = s.scalar(select(m.FileRecord).where(m.FileRecord.id == file_id).with_for_update(skip_locked=True))
            if f is None:
                continue
            storage.delete(f.object_key)
            if f.status == "deleting":
                s.delete(f)
            else:
                f.status, f.failure_code = "failed", "upload_incomplete"
            cleaned += 1
    return cleaned
