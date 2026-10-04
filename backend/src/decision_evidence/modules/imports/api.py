from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from decision_evidence.db import models as m
from decision_evidence.errors import ApiError, bad_request, conflict, not_found
from decision_evidence.jobs.service import enqueue_job
from decision_evidence.modules.common import Page, Paging, tenant_settings
from decision_evidence.modules.imports import service
from decision_evidence.modules.imports.domain import parsing as p
from decision_evidence.storage.port import new_object_key
from decision_evidence.storage.s3 import get_storage
from decision_evidence.tenancy.context import TenantContext, editor, viewer

router = APIRouter(prefix="/api/v1/workspaces/{tenant_id}/imports", tags=["imports"])

Kind = Literal["customers", "opportunities", "feedback", "document"]


class ImportOut(BaseModel):
    id: uuid.UUID
    kind: str
    status: str
    file_name: str
    file_size: int
    mapping: dict[str, Any]
    options: dict[str, Any]
    detected: dict[str, Any]
    preview: dict[str, Any] | None
    result: dict[str, Any] | None
    job_id: uuid.UUID | None
    created_at: datetime


def _out(batch: m.ImportBatch, file: m.FileRecord) -> ImportOut:
    return ImportOut(id=batch.id, kind=batch.kind, status=batch.status, file_name=file.original_name, file_size=file.byte_size,
                     mapping=batch.mapping, options=batch.options, detected=batch.detected, preview=batch.preview, result=batch.result,
                     job_id=batch.job_id, created_at=batch.created_at)


def _store_file(ctx: TenantContext, name: str, media_type: str, purpose: str, data: bytes) -> uuid.UUID:
    """Two-phase upload: row 'pending' (committed), object write, row 'ready'. Orphans are swept by the worker janitor."""
    key = new_object_key(ctx.tenant_id)
    import hashlib
    with ctx.session() as s:
        f = m.FileRecord(object_key=key, original_name=p.safe_filename(name), media_type=media_type, byte_size=len(data),
                         sha256=hashlib.sha256(data).hexdigest(), purpose=purpose, status="pending", uploaded_by=ctx.user_id)
        s.add(f)
        s.flush()
        file_id = f.id
    try:
        get_storage().put(key, data, media_type)
    except Exception as exc:
        with ctx.session() as s:
            f2 = s.get(m.FileRecord, file_id)
            if f2:
                f2.status, f2.failure_code = "failed", "storage_unavailable"
        raise ApiError(503, "storage_unavailable", "Der Dateispeicher ist nicht erreichbar") from exc
    return file_id


def _create(ctx: TenantContext, kind: str, name: str, media_type: str, purpose: str, data: bytes, options: dict[str, Any]) -> ImportOut:
    with ctx.session() as s:
        ts = tenant_settings(s)
        max_bytes, max_rows = ts.max_file_bytes, ts.max_import_rows
    if len(data) > max_bytes:
        raise ApiError(413, "file_too_large", "Datei zu groß", f"Erlaubt sind {max_bytes // 1024 // 1024} MB.")
    try:
        detected = service.detect_batch(kind, name, data, max_rows)
    except p.FileRejected as exc:
        raise ApiError(422, exc.code, "Datei nicht verwendbar", exc.message) from exc
    file_id = _store_file(ctx, name, media_type, purpose, data)
    with ctx.session() as s:
        f = s.get(m.FileRecord, file_id)
        assert f is not None
        f.status = "ready"
        batch = service.create_batch(s, ctx.user_id, kind, f, detected, service._norm_options(kind, options))
        ctx.audit(s, "import.uploaded", "import_batch", batch.id, kind=kind, bytes=len(data))
        return _out(batch, f)


@router.post("", response_model=ImportOut, status_code=201)
def upload(ctx: Annotated[TenantContext, editor], kind: Annotated[Kind, Form()], file: Annotated[UploadFile, File()],
           synthetic: Annotated[bool, Form()] = False) -> ImportOut:
    with ctx.session() as s:
        max_bytes = tenant_settings(s).max_file_bytes
    data = file.file.read(max_bytes + 1)
    name = file.filename or "upload"
    try:
        purpose, media_type = p.validate_upload(name, file.content_type, data[:max_bytes])
    except p.FileRejected as exc:
        raise ApiError(422, exc.code, "Datei nicht verwendbar", exc.message) from exc
    if kind in p.CSV_KINDS and purpose != "import_csv" or kind == "document" and purpose != "import_document":
        raise bad_request("kind_file_mismatch", "Dateityp passt nicht zur Importart")
    return _create(ctx, kind, name, media_type, purpose, data, {"synthetic": synthetic})


class TextNoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str = Field(min_length=10, max_length=100_000)
    customer_external_id: str | None = Field(default=None, max_length=100)
    occurred_at: str | None = None
    channel: Literal["call", "interview", "email", "chat", "survey", "other"] = "call"
    synthetic: bool = False


@router.post("/text", response_model=ImportOut, status_code=201)
def upload_text(ctx: Annotated[TenantContext, editor], body: TextNoteIn) -> ImportOut:
    data = body.text.encode()
    out = _create(ctx, "notes_text", f"{p.safe_filename(body.title)}.txt", "text/plain", "import_text", data,
                  {"title": body.title, "customer_external_id": body.customer_external_id, "occurred_at": body.occurred_at,
                   "channel": body.channel, "synthetic": body.synthetic})
    return _preview(ctx, out.id, None, None)


class SettingsIn(BaseModel):
    mapping: dict[str, str | None] | None = None
    options: dict[str, Any] | None = None


def _preview(ctx: TenantContext, batch_id: uuid.UUID, mapping: dict[str, str | None] | None, options: dict[str, Any] | None) -> ImportOut:
    with ctx.session() as s:
        batch = s.scalar(select(m.ImportBatch).where(m.ImportBatch.id == batch_id).with_for_update())
        if batch is None:
            raise not_found("Import")
        if batch.status in ("committing", "committed"):
            raise conflict("import_locked", "Der Import wurde bereits bestätigt")
        file = s.get(m.FileRecord, batch.file_id)
        assert file is not None
        if mapping is not None:
            batch.mapping = mapping
        if options is not None:
            batch.options = service._norm_options(batch.kind, {**batch.options, **options})
        key = file.object_key
        file_sha = file.sha256
    data = get_storage().get(key)
    with ctx.session() as s:
        batch = s.get(m.ImportBatch, batch_id)
        file = s.get(m.FileRecord, batch.file_id)  # type: ignore[union-attr]
        assert batch is not None and file is not None
        try:
            plan = service.compute_import_plan(s, batch, file, data)
        except p.FileRejected as exc:
            raise ApiError(422, exc.code, "Datei nicht verwendbar", exc.message) from exc
        batch.preview = service.summarise_plan(plan)
        batch.preview_hash = service.preview_hash(file_sha, batch.mapping, batch.options)
        batch.status = "previewed"
        return _out(batch, file)


@router.put("/{import_id}/settings", response_model=ImportOut)
def update_settings(ctx: Annotated[TenantContext, editor], import_id: uuid.UUID, body: SettingsIn) -> ImportOut:
    return _preview(ctx, import_id, body.mapping, body.options)


@router.get("", response_model=Page[ImportOut])
def list_imports(ctx: Annotated[TenantContext, viewer], paging: Annotated[Paging, Depends()]) -> Page[ImportOut]:
    with ctx.session() as s:
        total = s.scalar(select(func.count()).select_from(m.ImportBatch)) or 0
        rows = s.execute(select(m.ImportBatch, m.FileRecord).join(m.FileRecord, (m.FileRecord.id == m.ImportBatch.file_id) & (m.FileRecord.tenant_id == m.ImportBatch.tenant_id))
                         .order_by(m.ImportBatch.created_at.desc(), m.ImportBatch.id).limit(paging.limit).offset(paging.offset)).all()
        return Page[ImportOut](items=[_out(b, f) for b, f in rows], total=total, limit=paging.limit, offset=paging.offset)


@router.get("/{import_id}", response_model=ImportOut)
def get_import(ctx: Annotated[TenantContext, viewer], import_id: uuid.UUID) -> ImportOut:
    with ctx.session() as s:
        row = s.execute(select(m.ImportBatch, m.FileRecord).join(m.FileRecord, (m.FileRecord.id == m.ImportBatch.file_id) & (m.FileRecord.tenant_id == m.ImportBatch.tenant_id))
                        .where(m.ImportBatch.id == import_id)).first()
        if row is None:
            raise not_found("Import")
        return _out(*row)


class JobRef(BaseModel):
    job_id: uuid.UUID
    status_url: str
    status: str


@router.post("/{import_id}/commit", response_model=JobRef, status_code=202)
def commit(ctx: Annotated[TenantContext, editor], import_id: uuid.UUID) -> JobRef:
    with ctx.session() as s:
        batch = s.scalar(select(m.ImportBatch).where(m.ImportBatch.id == import_id).with_for_update())
        if batch is None:
            raise not_found("Import")
        if batch.job_id is not None and batch.status in ("committing", "committed"):
            job = s.get(m.Job, batch.job_id)
            assert job is not None
            return JobRef(job_id=job.id, status_url=_status_url(ctx, job.id), status=job.status)
        if batch.status != "previewed" or not batch.preview:
            raise conflict("not_previewed", "Der Import hat noch keine aktuelle Vorschau")
        if batch.preview.get("blocking"):
            raise conflict("preview_blocking", "Der Import enthält Fehler und kann nicht bestätigt werden",
                           "; ".join(batch.preview.get("blocking_reasons", [])))
        ts = tenant_settings(s)
        job, _ = enqueue_job(s, kind="import_commit", idempotency_key=f"import:{batch.id}", payload={"batch_id": str(batch.id)},
                             requested_by=ctx.user_id, max_running=ts.max_running_jobs)
        batch.status, batch.job_id = "committing", job.id
        ctx.audit(s, "import.commit_requested", "import_batch", batch.id, kind=batch.kind)
        return JobRef(job_id=job.id, status_url=_status_url(ctx, job.id), status=job.status)


def _status_url(ctx: TenantContext, job_id: uuid.UUID) -> str:
    return f"/api/v1/workspaces/{ctx.tenant_id}/jobs/{job_id}"
