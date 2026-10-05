"""Creating jobs (API side). Job and outbox row are written in the caller's transaction."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from decision_evidence.db import models as m
from decision_evidence.db.context import current_request_id
from decision_evidence.errors import ApiError

ACTIVE = ("queued", "running")


def add_outbox(s: Session, job_id: uuid.UUID, available_at: datetime | None = None) -> None:
    s.add(m.Outbox(job_id=job_id, correlation_id=current_request_id.get(), available_at=available_at or datetime.now(UTC)))


def enqueue_job(s: Session, *, kind: str, idempotency_key: str, payload: dict[str, Any], requested_by: uuid.UUID | None,
                max_running: int | None = None) -> tuple[m.Job, bool]:
    """Returns (job, created). Same (kind, idempotency_key) returns the existing job and creates nothing."""
    existing = s.scalar(select(m.Job).where(m.Job.kind == kind, m.Job.idempotency_key == idempotency_key))
    if existing is not None:
        return existing, False
    if max_running is not None:
        running = s.scalar(select(func.count()).select_from(m.Job).where(m.Job.status.in_(ACTIVE))) or 0
        if running >= max_running:
            raise ApiError(429, "too_many_jobs", "Zu viele laufende Aufgaben",
                           f"Es laufen bereits {running} Aufgaben (Limit {max_running}). Bitte später erneut versuchen.")
    stmt = (pg_insert(m.Job).values(
        id=uuid.uuid4(), tenant_id=s.info["tenant_id"], kind=kind, idempotency_key=idempotency_key, payload=payload,
        requested_by=requested_by, correlation_id=current_request_id.get())
        .on_conflict_do_nothing(index_elements=["tenant_id", "kind", "idempotency_key"])
        .returning(m.Job.id))
    new_id = s.execute(stmt).scalar()
    if new_id is None:  # lost a race against a concurrent identical request
        existing = s.scalar(select(m.Job).where(m.Job.kind == kind, m.Job.idempotency_key == idempotency_key))
        assert existing is not None
        return existing, False
    job = s.get(m.Job, new_id)
    assert job is not None
    add_outbox(s, job.id)
    s.flush()
    return job, True


def retry_failed_job(s: Session, job: m.Job) -> m.Job:
    if job.status != "failed":
        raise ApiError(409, "job_not_failed", "Nur fehlgeschlagene Aufgaben können wiederholt werden")
    job.status, job.attempts, job.error_code, job.error_message = "queued", 0, None, None
    job.finished_at = job.lease_expires_at = job.claim_token = None
    job.progress = 0
    add_outbox(s, job.id)
    s.flush()
    return job
