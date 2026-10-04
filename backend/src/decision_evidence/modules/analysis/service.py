from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, exists, func, select
from sqlalchemy.orm import Session

from decision_evidence.config import Settings
from decision_evidence.db import models as m
from decision_evidence.errors import ApiError


def month_start() -> datetime:
    n = datetime.now(UTC)
    return n.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def ai_calls_this_month(s: Session) -> int:
    return s.scalar(select(func.count()).select_from(m.AiRun).where(m.AiRun.created_at >= month_start())) or 0


def check_budget(s: Session, budget: int) -> None:
    used = ai_calls_this_month(s)
    if used >= budget:
        raise ApiError(429, "ai_budget_exhausted", "Das KI-Aufrufbudget dieses Monats ist aufgebraucht",
                       f"{used} von {budget} Aufrufen verbraucht. Ein Admin kann das Budget in den Workspace-Einstellungen anpassen.")


def eligible_chunk_ids(s: Session, scope: str, limit: int) -> tuple[list[uuid.UUID], bool]:
    """Newest chunks first. scope=unassigned skips chunks that already belong to a non-archived problem."""
    cond = []
    if scope == "unassigned":
        assigned = exists().where(and_(m.ProblemEvidence.source_chunk_id == m.SourceChunk.id, m.Problem.id == m.ProblemEvidence.problem_id,
                                       m.Problem.status != "archived"))
        cond.append(~assigned)
    q = (select(m.SourceChunk.id).join(m.FeedbackItem, m.FeedbackItem.source_record_id == m.SourceChunk.source_record_id)
         .where(*cond).order_by(m.FeedbackItem.occurred_at.desc(), m.SourceChunk.id).limit(limit + 1))
    ids = list(s.scalars(q))
    return ids[:limit], len(ids) > limit


def analysis_key(settings: Settings, scope: str, chunk_ids: list[uuid.UUID]) -> str:
    h = hashlib.sha256()
    for part in (scope, settings.ai_provider, settings.anthropic_model if settings.ai_provider == "anthropic" else "mock", *sorted(str(c) for c in chunk_ids)):
        h.update(part.encode())
        h.update(b"|")
    return "analysis:" + h.hexdigest()[:40]


def provider_label(settings: Settings) -> dict[str, Any]:
    if settings.ai_provider == "mock":
        return {"provider": "mock", "model": "demo-deterministic-v1", "demo": True}
    return {"provider": "anthropic", "model": settings.anthropic_model, "demo": False}
