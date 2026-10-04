"""Read APIs for customers, opportunities, feedback search, sources and file downloads."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import and_, exists, func, or_, select, text

from decision_evidence.db import models as m
from decision_evidence.errors import conflict, not_found
from decision_evidence.modules.common import Page, Paging
from decision_evidence.storage.port import ObjectNotFound, assert_key_in_tenant
from decision_evidence.storage.s3 import get_storage
from decision_evidence.tenancy.context import TenantContext, admin, viewer

router = APIRouter(prefix="/api/v1/workspaces/{tenant_id}", tags=["catalog"])


class CustomerOut(BaseModel):
    id: uuid.UUID
    external_id: str
    name: str
    segment: str | None
    country: str | None
    commercial_value: Decimal | None
    value_basis: str
    currency: str | None
    value_as_of: Any | None
    feedback_count: int
    opportunity_count: int


def _like(q: str) -> str:
    return "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


@router.get("/customers", response_model=Page[CustomerOut])
def list_customers(ctx: Annotated[TenantContext, viewer], paging: Annotated[Paging, Depends()], q: Annotated[str | None, Query(max_length=200)] = None,
                   segment: str | None = None, value_known: bool | None = None) -> Page[CustomerOut]:
    with ctx.session() as s:
        cond = []
        if q:
            cond.append(or_(m.CustomerAccount.name.ilike(_like(q)), m.CustomerAccount.external_id.ilike(_like(q))))
        if segment:
            cond.append(m.CustomerAccount.segment == segment)
        if value_known is not None:
            cond.append((m.CustomerAccount.commercial_value.is_not(None)) if value_known else m.CustomerAccount.commercial_value.is_(None))
        total = s.scalar(select(func.count()).select_from(m.CustomerAccount).where(*cond)) or 0
        fb = select(m.FeedbackItem.customer_account_id, func.count().label("n")).group_by(m.FeedbackItem.customer_account_id).subquery()
        op = select(m.Opportunity.customer_account_id, func.count().label("n")).group_by(m.Opportunity.customer_account_id).subquery()
        rows = s.execute(select(m.CustomerAccount, func.coalesce(fb.c.n, 0), func.coalesce(op.c.n, 0))
                         .outerjoin(fb, fb.c.customer_account_id == m.CustomerAccount.id).outerjoin(op, op.c.customer_account_id == m.CustomerAccount.id)
                         .where(*cond).order_by(m.CustomerAccount.name, m.CustomerAccount.id).limit(paging.limit).offset(paging.offset)).all()
        items = [CustomerOut(id=c.id, external_id=c.external_id, name=c.name, segment=c.segment, country=c.country, commercial_value=c.commercial_value,
                             value_basis=c.value_basis, currency=c.currency, value_as_of=c.value_as_of, feedback_count=f, opportunity_count=o) for c, f, o in rows]
        return Page[CustomerOut](items=items, total=total, limit=paging.limit, offset=paging.offset)


class OpportunityOut(BaseModel):
    id: uuid.UUID
    external_id: str
    customer_account_id: uuid.UUID
    customer_name: str
    name: str
    stage: str
    amount: Decimal | None
    currency: str | None
    closed_at: Any | None


@router.get("/opportunities", response_model=Page[OpportunityOut])
def list_opportunities(ctx: Annotated[TenantContext, viewer], paging: Annotated[Paging, Depends()], stage: Literal["open", "won", "lost", "no_decision"] | None = None,
                       customer_id: uuid.UUID | None = None, q: Annotated[str | None, Query(max_length=200)] = None) -> Page[OpportunityOut]:
    with ctx.session() as s:
        cond = []
        if stage:
            cond.append(m.Opportunity.stage == stage)
        if customer_id:
            cond.append(m.Opportunity.customer_account_id == customer_id)
        if q:
            cond.append(or_(m.Opportunity.name.ilike(_like(q)), m.Opportunity.external_id.ilike(_like(q))))
        total = s.scalar(select(func.count()).select_from(m.Opportunity).where(*cond)) or 0
        rows = s.execute(select(m.Opportunity, m.CustomerAccount.name).join(m.CustomerAccount, m.CustomerAccount.id == m.Opportunity.customer_account_id)
                         .where(*cond).order_by(m.Opportunity.stage, m.Opportunity.external_id).limit(paging.limit).offset(paging.offset)).all()
        return Page[OpportunityOut](items=[OpportunityOut(id=o.id, external_id=o.external_id, customer_account_id=o.customer_account_id, customer_name=n, name=o.name,
                                                          stage=o.stage, amount=o.amount, currency=o.currency, closed_at=o.closed_at) for o, n in rows],
                                    total=total, limit=paging.limit, offset=paging.offset)


class CustomerDetail(CustomerOut):
    opportunities: list[OpportunityOut]
    feedback: list[dict[str, Any]]
    problems: list[dict[str, Any]]


@router.get("/customers/{customer_id}", response_model=CustomerDetail)
def get_customer(ctx: Annotated[TenantContext, viewer], customer_id: uuid.UUID) -> CustomerDetail:
    with ctx.session() as s:
        c = s.get(m.CustomerAccount, customer_id)
        if c is None:
            raise not_found("Kunde")
        opps = list(s.scalars(select(m.Opportunity).where(m.Opportunity.customer_account_id == c.id).order_by(m.Opportunity.external_id)))
        fb = list(s.scalars(select(m.FeedbackItem).where(m.FeedbackItem.customer_account_id == c.id).order_by(m.FeedbackItem.occurred_at.desc()).limit(50)))
        fb_count = s.scalar(select(func.count()).select_from(m.FeedbackItem).where(m.FeedbackItem.customer_account_id == c.id)) or 0
        probs = s.execute(select(m.Problem.id, m.Problem.title, m.Problem.status, func.count(m.ProblemEvidence.id))
                          .join(m.ProblemEvidence, m.ProblemEvidence.problem_id == m.Problem.id)
                          .join(m.FeedbackItem, m.FeedbackItem.id == m.ProblemEvidence.feedback_item_id)
                          .where(m.FeedbackItem.customer_account_id == c.id, m.ProblemEvidence.relation == "supports").group_by(m.Problem.id)).all()
        return CustomerDetail(
            id=c.id, external_id=c.external_id, name=c.name, segment=c.segment, country=c.country, commercial_value=c.commercial_value,
            value_basis=c.value_basis, currency=c.currency, value_as_of=c.value_as_of, feedback_count=fb_count, opportunity_count=len(opps),
            opportunities=[OpportunityOut(id=o.id, external_id=o.external_id, customer_account_id=o.customer_account_id, customer_name=c.name, name=o.name,
                                          stage=o.stage, amount=o.amount, currency=o.currency, closed_at=o.closed_at) for o in opps],
            feedback=[{"id": str(f.id), "occurred_at": f.occurred_at.isoformat(), "channel": f.channel, "body": f.body[:400]} for f in fb],
            problems=[{"id": str(pid), "title": t, "status": st, "statements": n} for pid, t, st, n in probs])


class FeedbackOut(BaseModel):
    id: uuid.UUID
    source_record_id: uuid.UUID
    chunk_ids: list[uuid.UUID]
    customer_id: uuid.UUID | None
    customer_name: str | None
    channel: str
    occurred_at: datetime
    body: str
    external_id: str | None
    problem_ids: list[uuid.UUID]
    source_origin: str


@router.get("/feedback", response_model=Page[FeedbackOut])
def search_feedback(ctx: Annotated[TenantContext, viewer], paging: Annotated[Paging, Depends()], q: Annotated[str | None, Query(max_length=200)] = None,
                    customer_id: uuid.UUID | None = None, channel: str | None = None, assigned: bool | None = None,
                    problem_id: uuid.UUID | None = None, date_from: datetime | None = None, date_to: datetime | None = None) -> Page[FeedbackOut]:
    with ctx.session() as s:
        cond = []
        order = [m.FeedbackItem.occurred_at.desc(), m.FeedbackItem.id]
        if q and q.strip():
            tsq = func.websearch_to_tsquery("german", q)
            cond.append(or_(m.FeedbackItem.search_vector.op("@@")(tsq), m.FeedbackItem.body.ilike(_like(q.strip()))))
            order = [func.ts_rank(m.FeedbackItem.search_vector, tsq).desc(), m.FeedbackItem.occurred_at.desc(), m.FeedbackItem.id]
        if customer_id:
            cond.append(m.FeedbackItem.customer_account_id == customer_id)
        if channel:
            cond.append(m.FeedbackItem.channel == channel)
        if date_from:
            cond.append(m.FeedbackItem.occurred_at >= date_from)
        if date_to:
            cond.append(m.FeedbackItem.occurred_at <= date_to)
        in_problem = exists().where(and_(m.ProblemEvidence.feedback_item_id == m.FeedbackItem.id, m.Problem.id == m.ProblemEvidence.problem_id, m.Problem.status != "archived"))
        if assigned is not None:
            cond.append(in_problem if assigned else ~in_problem)
        if problem_id:
            cond.append(exists().where(and_(m.ProblemEvidence.feedback_item_id == m.FeedbackItem.id, m.ProblemEvidence.problem_id == problem_id)))
        total = s.scalar(select(func.count()).select_from(m.FeedbackItem).where(*cond)) or 0
        rows = s.execute(select(m.FeedbackItem, m.CustomerAccount.name, m.SourceRecord.origin)
                         .outerjoin(m.CustomerAccount, m.CustomerAccount.id == m.FeedbackItem.customer_account_id)
                         .join(m.SourceRecord, m.SourceRecord.id == m.FeedbackItem.source_record_id)
                         .where(*cond).order_by(*order).limit(paging.limit).offset(paging.offset)).all()
        ids = [f.id for f, _, _ in rows]
        sources = [f.source_record_id for f, _, _ in rows]
        chunks: dict[uuid.UUID, list[uuid.UUID]] = {}
        for sid, cid in s.execute(select(m.SourceChunk.source_record_id, m.SourceChunk.id).where(m.SourceChunk.source_record_id.in_(sources)).order_by(m.SourceChunk.ordinal)):
            chunks.setdefault(sid, []).append(cid)
        probs: dict[uuid.UUID, list[uuid.UUID]] = {}
        for fid, pid in s.execute(select(m.ProblemEvidence.feedback_item_id, m.ProblemEvidence.problem_id).where(m.ProblemEvidence.feedback_item_id.in_(ids))):
            probs.setdefault(fid, []).append(pid)
        items = [FeedbackOut(id=f.id, source_record_id=f.source_record_id, chunk_ids=chunks.get(f.source_record_id, []), customer_id=f.customer_account_id,
                             customer_name=cn, channel=f.channel, occurred_at=f.occurred_at, body=f.body[:1200], external_id=f.external_id,
                             problem_ids=probs.get(f.id, []), source_origin=origin) for f, cn, origin in rows]
        return Page[FeedbackOut](items=items, total=total, limit=paging.limit, offset=paging.offset)


class SourceOut(BaseModel):
    id: uuid.UUID
    source_kind: str
    origin: str
    title: str
    external_id: str | None
    source_timestamp: datetime | None
    file_id: uuid.UUID | None
    file_name: str | None
    chunk_count: int
    created_at: datetime


class ChunkOut(BaseModel):
    id: uuid.UUID
    ordinal: int
    text: str
    locator: dict[str, Any]
    problem_ids: list[uuid.UUID]


class SourceDetail(SourceOut):
    chunks: list[ChunkOut]
    customer_id: uuid.UUID | None
    customer_name: str | None
    metadata: dict[str, Any]


@router.get("/sources", response_model=Page[SourceOut])
def list_sources(ctx: Annotated[TenantContext, viewer], paging: Annotated[Paging, Depends()], q: Annotated[str | None, Query(max_length=200)] = None,
                 kind: Literal["csv_feedback", "note_text", "document"] | None = None, origin: Literal["original", "imported", "synthetic"] | None = None) -> Page[SourceOut]:
    with ctx.session() as s:
        cond = []
        if q:
            cond.append(m.SourceRecord.title.ilike(_like(q)))
        if kind:
            cond.append(m.SourceRecord.source_kind == kind)
        if origin:
            cond.append(m.SourceRecord.origin == origin)
        total = s.scalar(select(func.count()).select_from(m.SourceRecord).where(*cond)) or 0
        cc = select(m.SourceChunk.source_record_id, func.count().label("n")).group_by(m.SourceChunk.source_record_id).subquery()
        rows = s.execute(select(m.SourceRecord, m.FileRecord.original_name, func.coalesce(cc.c.n, 0))
                         .outerjoin(m.FileRecord, m.FileRecord.id == m.SourceRecord.file_id).outerjoin(cc, cc.c.source_record_id == m.SourceRecord.id)
                         .where(*cond).order_by(m.SourceRecord.created_at.desc(), m.SourceRecord.id).limit(paging.limit).offset(paging.offset)).all()
        return Page[SourceOut](items=[SourceOut(id=r.id, source_kind=r.source_kind, origin=r.origin, title=r.title, external_id=r.external_id,
                                                source_timestamp=r.source_timestamp, file_id=r.file_id, file_name=fn, chunk_count=n, created_at=r.created_at)
                                      for r, fn, n in rows], total=total, limit=paging.limit, offset=paging.offset)


@router.get("/sources/{source_id}", response_model=SourceDetail)
def get_source(ctx: Annotated[TenantContext, viewer], source_id: uuid.UUID) -> SourceDetail:
    with ctx.session() as s:
        r = s.get(m.SourceRecord, source_id)
        if r is None:
            raise not_found("Quelle")
        fn = s.scalar(select(m.FileRecord.original_name).where(m.FileRecord.id == r.file_id)) if r.file_id else None
        chunks = list(s.scalars(select(m.SourceChunk).where(m.SourceChunk.source_record_id == r.id).order_by(m.SourceChunk.ordinal)))
        probs: dict[uuid.UUID, list[uuid.UUID]] = {}
        for cid, pid in s.execute(select(m.ProblemEvidence.source_chunk_id, m.ProblemEvidence.problem_id).where(m.ProblemEvidence.source_chunk_id.in_([c.id for c in chunks]))):
            probs.setdefault(cid, []).append(pid)
        fi = s.scalar(select(m.FeedbackItem).where(m.FeedbackItem.source_record_id == r.id))
        cname = s.scalar(select(m.CustomerAccount.name).where(m.CustomerAccount.id == fi.customer_account_id)) if fi and fi.customer_account_id else None
        return SourceDetail(id=r.id, source_kind=r.source_kind, origin=r.origin, title=r.title, external_id=r.external_id, source_timestamp=r.source_timestamp,
                            file_id=r.file_id, file_name=fn, chunk_count=len(chunks), created_at=r.created_at, metadata=r.metadata_,
                            customer_id=fi.customer_account_id if fi else None, customer_name=cname,
                            chunks=[ChunkOut(id=c.id, ordinal=c.ordinal, text=c.text, locator=c.locator, problem_ids=probs.get(c.id, [])) for c in chunks])


@router.get("/chunks/{chunk_id}", response_model=ChunkOut)
def get_chunk(ctx: Annotated[TenantContext, viewer], chunk_id: uuid.UUID) -> ChunkOut:
    """Resolves a chunk id (e.g. from an evidence link). A chunk of another tenant is simply 404."""
    with ctx.session() as s:
        c = s.get(m.SourceChunk, chunk_id)
        if c is None:
            raise not_found("Beleg")
        probs = list(s.scalars(select(m.ProblemEvidence.problem_id).where(m.ProblemEvidence.source_chunk_id == c.id)))
        return ChunkOut(id=c.id, ordinal=c.ordinal, text=c.text, locator=c.locator, problem_ids=probs)


@router.delete("/sources/{source_id}", status_code=200)
def delete_source(ctx: Annotated[TenantContext, admin], source_id: uuid.UUID) -> dict[str, Any]:
    """Deletes a source with its chunks, feedback item and derived problem evidence. Refused (409) while an assumption or a
    decision revision still cites it, so approved decisions never lose their evidence."""
    with ctx.session() as s:
        r = s.get(m.SourceRecord, source_id)
        if r is None:
            raise not_found("Quelle")
        chunk_ids = list(s.scalars(select(m.SourceChunk.id).where(m.SourceChunk.source_record_id == r.id)))
        blockers: list[dict[str, str]] = []
        for doc_id, state in s.execute(select(m.DecisionDocument.id, m.DecisionDocument.state).join(m.DecisionEvidence, m.DecisionEvidence.decision_document_id == m.DecisionDocument.id)
                                       .where(m.DecisionEvidence.source_chunk_id.in_(chunk_ids)).distinct()):
            blockers.append({"type": "decision_document", "id": str(doc_id), "state": state})
        for aid in s.scalars(select(m.InitiativeAssumption.id).where(m.InitiativeAssumption.source_chunk_id.in_(chunk_ids))):
            blockers.append({"type": "assumption", "id": str(aid)})
        if blockers:
            raise conflict("source_in_use", "Die Quelle wird noch verwendet", "Entscheidungsdokumente und Annahmen verweisen auf diese Quelle.", blockers=blockers)
        evidence_n = s.scalar(select(func.count()).select_from(m.ProblemEvidence).where(m.ProblemEvidence.source_chunk_id.in_(chunk_ids))) or 0
        affected = list(s.scalars(select(m.ProblemEvidence.problem_id).where(m.ProblemEvidence.source_chunk_id.in_(chunk_ids)).distinct()))
        from decision_evidence.modules.problems.service import bump, get_problem, log_history
        for pid in affected:
            p = get_problem(s, pid, lock=True)
            log_history(s, pid, "evidence_removed", ctx.user_id, reason="source_deleted", source_id=source_id)
            bump(p)
        s.execute(text("DELETE FROM app.feedback_items WHERE source_record_id = :i"), {"i": source_id})  # cascades problem_evidence
        s.delete(r)
        ctx.audit(s, "source.deleted", "source_record", source_id, chunks=len(chunk_ids), evidence_removed=evidence_n, title=r.title)
        return {"deleted": True, "chunks": len(chunk_ids), "evidence_removed": evidence_n, "problems_affected": len(affected)}


@router.get("/files/{file_id}/download")
def download_file(ctx: Annotated[TenantContext, viewer], file_id: uuid.UUID) -> StreamingResponse:
    """Streams a stored upload through the API. The storage key is never taken from the browser and never exposed."""
    with ctx.session() as s:
        f = s.get(m.FileRecord, file_id)
        if f is None or f.status != "ready":
            raise not_found("Datei")
        key, name, media_type, size = f.object_key, f.original_name, f.media_type, f.byte_size
        assert_key_in_tenant(key, ctx.tenant_id)
        ctx.audit(s, "file.downloaded", "file", file_id)
    storage = get_storage()
    try:
        stream = storage.stream(key)
        first = next(stream, b"")
    except ObjectNotFound as exc:
        raise not_found("Datei") from exc
    from itertools import chain
    from urllib.parse import quote
    safe_type = media_type if media_type in {"text/csv", "text/plain", "application/pdf"} else "application/octet-stream"
    return StreamingResponse(chain([first], stream), media_type=safe_type, headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}", "Content-Length": str(size), "X-Content-Type-Options": "nosniff"})


