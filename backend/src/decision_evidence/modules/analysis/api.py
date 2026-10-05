from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select

from decision_evidence.config import get_settings
from decision_evidence.db import models as m
from decision_evidence.errors import ApiError, not_found
from decision_evidence.jobs.service import enqueue_job, retry_failed_job
from decision_evidence.modules.analysis import service
from decision_evidence.modules.common import Page, Paging, tenant_settings
from decision_evidence.tenancy.context import TenantContext, editor, viewer

router = APIRouter(prefix="/api/v1/workspaces/{tenant_id}", tags=["jobs", "analysis"])


class JobOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    progress: int
    attempts: int
    max_attempts: int
    error_code: str | None
    error_message: str | None
    result: dict[str, Any] | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    status_url: str


def job_out(ctx: TenantContext, j: m.Job) -> JobOut:
    return JobOut(id=j.id, kind=j.kind, status=j.status, progress=j.progress, attempts=j.attempts, max_attempts=j.max_attempts, error_code=j.error_code,
                  error_message=j.error_message, result=j.result, created_at=j.created_at, started_at=j.started_at, finished_at=j.finished_at,
                  status_url=f"/api/v1/workspaces/{ctx.tenant_id}/jobs/{j.id}")


@router.get("/jobs", response_model=Page[JobOut])
def list_jobs(ctx: Annotated[TenantContext, viewer], paging: Annotated[Paging, Depends()], status: Literal["queued", "running", "succeeded", "failed"] | None = None,
              kind: Literal["import_commit", "analyze_feedback", "draft_rationale"] | None = None) -> Page[JobOut]:
    with ctx.session() as s:
        cond = [m.Job.status == status] if status else []
        if kind:
            cond.append(m.Job.kind == kind)
        total = s.scalar(select(func.count()).select_from(m.Job).where(*cond)) or 0
        rows = s.scalars(select(m.Job).where(*cond).order_by(m.Job.created_at.desc(), m.Job.id).limit(paging.limit).offset(paging.offset))
        return Page[JobOut](items=[job_out(ctx, j) for j in rows], total=total, limit=paging.limit, offset=paging.offset)


@router.get("/jobs/{job_id}", response_model=JobOut)
def get_job(ctx: Annotated[TenantContext, viewer], job_id: uuid.UUID) -> JobOut:
    with ctx.session() as s:
        j = s.get(m.Job, job_id)
        if j is None:
            raise not_found("Aufgabe")
        return job_out(ctx, j)


@router.post("/jobs/{job_id}/retry", response_model=JobOut, status_code=202)
def retry_job(ctx: Annotated[TenantContext, editor], job_id: uuid.UUID) -> JobOut:
    with ctx.session() as s:
        j = s.scalar(select(m.Job).where(m.Job.id == job_id).with_for_update())
        if j is None:
            raise not_found("Aufgabe")
        retry_failed_job(s, j)
        ctx.audit(s, "job.retried", "job", j.id, kind=j.kind)
        return job_out(ctx, j)


class AnalysisIn(BaseModel):
    scope: Literal["unassigned", "all"] = "unassigned"


class AnalysisAccepted(BaseModel):
    job: JobOut
    created: bool
    truncated: bool
    chunk_count: int
    provider: dict[str, Any]


@router.post("/analysis", response_model=AnalysisAccepted, status_code=202)
def start_analysis(ctx: Annotated[TenantContext, editor], body: AnalysisIn) -> AnalysisAccepted:
    settings = get_settings()
    with ctx.session() as s:
        ts = tenant_settings(s)
        service.check_budget(s, ts.ai_monthly_call_budget)
        ids, truncated = service.eligible_chunk_ids(s, body.scope, ts.max_ai_context_chunks)
        if not ids:
            raise ApiError(422, "nothing_to_analyze", "Es gibt keine Belege zum Analysieren",
                           "Alle importierten Aussagen sind bereits Problemen zugeordnet. Importieren Sie weiteres Feedback oder wählen Sie „alle Belege“.")
        key = service.analysis_key(settings, body.scope, ids)
        payload = {"scope": body.scope, "chunk_ids": [str(i) for i in ids], "truncated": truncated,
                   "input_hash": hashlib.sha256("".join(sorted(str(i) for i in ids)).encode()).hexdigest()}
        job, created = enqueue_job(s, kind="analyze_feedback", idempotency_key=key, payload=payload, requested_by=ctx.user_id, max_running=ts.max_running_jobs)
        if created:
            ctx.audit(s, "analysis.requested", "job", job.id, chunks=len(ids), provider=settings.ai_provider, truncated=truncated)
        return AnalysisAccepted(job=job_out(ctx, job), created=created, truncated=truncated, chunk_count=len(ids), provider=service.provider_label(settings))
