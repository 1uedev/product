"""Problem lifecycle, evidence corrections, merge and split with a traceable history."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from decision_evidence.db import models as m
from decision_evidence.errors import bad_request, conflict, not_found
from decision_evidence.modules.common import now
from decision_evidence.ai.verification import find_verbatim

RELATIONS = ("supports", "contradicts", "context")


def log_history(s: Session, problem_id: uuid.UUID, action: str, actor: uuid.UUID | None, **details: Any) -> None:
    s.add(m.ProblemHistory(problem_id=problem_id, action=action, actor_user_id=actor, details={k: _j(v) for k, v in details.items()}))


def _j(v: Any) -> Any:
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, (list, tuple)):
        return [_j(x) for x in v]
    if isinstance(v, dict):
        return {k: _j(x) for k, x in v.items()}
    return v


def get_problem(s: Session, problem_id: uuid.UUID, lock: bool = False) -> m.Problem:
    q = select(m.Problem).where(m.Problem.id == problem_id)
    if lock:
        q = q.with_for_update()
    p = s.scalar(q)
    if p is None:
        raise not_found("Problem")
    return p


def bump(p: m.Problem) -> None:
    p.version += 1


def move_evidence(s: Session, ev_ids: list[uuid.UUID], source: m.Problem, target: m.Problem, actor: uuid.UUID) -> tuple[int, int]:
    """Moves evidence between problems. If the target already holds the same chunk the duplicate is dropped.
    Returns (moved, merged_into_existing)."""
    moved = dedup = 0
    existing = set(s.scalars(select(m.ProblemEvidence.source_chunk_id).where(m.ProblemEvidence.problem_id == target.id)))
    for ev in s.scalars(select(m.ProblemEvidence).where(m.ProblemEvidence.id.in_(ev_ids), m.ProblemEvidence.problem_id == source.id)):
        if ev.source_chunk_id in existing:
            s.delete(ev)
            dedup += 1
        else:
            ev.problem_id = target.id
            existing.add(ev.source_chunk_id)
            moved += 1
    s.flush()
    return moved, dedup


def merge_problems(s: Session, target_id: uuid.UUID, source_ids: list[uuid.UUID], actor: uuid.UUID) -> m.Problem:
    if target_id in source_ids:
        raise bad_request("merge_self", "Ein Problem kann nicht mit sich selbst zusammengeführt werden")
    target = get_problem(s, target_id, lock=True)
    if target.status == "archived":
        raise conflict("target_archived", "Das Zielproblem ist archiviert")
    for sid in sorted(set(source_ids)):
        src = get_problem(s, sid, lock=True)
        if src.status == "archived":
            raise conflict("source_archived", f"„{src.title}“ ist bereits archiviert")
        ev_ids = list(s.scalars(select(m.ProblemEvidence.id).where(m.ProblemEvidence.problem_id == src.id)))
        moved, dedup = move_evidence(s, ev_ids, src, target, actor)
        inits = s.execute(update(m.Initiative).where(m.Initiative.problem_id == src.id).values(problem_id=target.id)).rowcount
        src.status, src.merged_into_problem_id = "archived", target.id
        bump(src)
        s.flush()
        log_history(s, src.id, "merged_into", actor, target_id=target.id, target_title=target.title, evidence_moved=moved, duplicates_dropped=dedup,
                    initiatives_moved=inits)
        log_history(s, target.id, "merged_from", actor, source_id=src.id, source_title=src.title, evidence_moved=moved, duplicates_dropped=dedup,
                    initiatives_moved=inits)
    bump(target)
    return target


def split_problem(s: Session, problem_id: uuid.UUID, evidence_ids: list[uuid.UUID], title: str, description: str, actor: uuid.UUID) -> m.Problem:
    src = get_problem(s, problem_id, lock=True)
    ids = set(evidence_ids)
    own = set(s.scalars(select(m.ProblemEvidence.id).where(m.ProblemEvidence.problem_id == src.id)))
    if not ids or not ids <= own:
        raise bad_request("evidence_not_in_problem", "Die gewählten Belege gehören nicht zu diesem Problem")
    if ids == own:
        raise conflict("split_all", "Alle Belege zu verschieben ist keine Aufteilung. Bitte stattdessen das Problem umbenennen.")
    new = m.Problem(title=title, description=description, status="proposed", origin="split", created_by=actor)
    s.add(new)
    s.flush()
    moved, _ = move_evidence(s, list(ids), src, new, actor)
    bump(src)
    log_history(s, new.id, "split_from", actor, source_id=src.id, source_title=src.title, evidence_moved=moved)
    log_history(s, src.id, "split_to", actor, target_id=new.id, target_title=title, evidence_moved=moved)
    return new


def add_evidence(s: Session, problem: m.Problem, chunk_id: uuid.UUID, relation: str, quote: str | None, actor: uuid.UUID) -> m.ProblemEvidence:
    chunk = s.get(m.SourceChunk, chunk_id)
    if chunk is None:
        raise not_found("Beleg")
    fi = s.scalar(select(m.FeedbackItem).where(m.FeedbackItem.source_record_id == chunk.source_record_id))
    if fi is None:
        raise bad_request("no_feedback_item", "Zu dieser Quelle gibt es keinen Feedbackeintrag")
    if s.scalar(select(m.ProblemEvidence.id).where(m.ProblemEvidence.problem_id == problem.id, m.ProblemEvidence.source_chunk_id == chunk_id)):
        raise conflict("evidence_exists", "Dieser Beleg gehört bereits zum Problem")
    if quote:
        exact = find_verbatim(chunk.text, quote)
        if exact is None:
            raise bad_request("quote_not_verbatim", "Das Zitat steht nicht wörtlich im Originaltext")
    else:
        exact = chunk.text[:400]
    ev = m.ProblemEvidence(problem_id=problem.id, feedback_item_id=fi.id, source_chunk_id=chunk_id, relation=relation, extracted_quote=exact,
                           origin="human", human_verified=True, verified_by=actor, verified_at=now())
    s.add(ev)
    s.flush()
    bump(problem)
    log_history(s, problem.id, "evidence_added", actor, evidence_id=ev.id, chunk_id=chunk_id, relation=relation)
    return ev


def update_evidence(s: Session, ev_id: uuid.UUID, actor: uuid.UUID, relation: str | None, verified: bool | None,
                    move_to: uuid.UUID | None) -> m.ProblemEvidence:
    ev = s.scalar(select(m.ProblemEvidence).where(m.ProblemEvidence.id == ev_id).with_for_update())
    if ev is None:
        raise not_found("Beleg")
    problem = get_problem(s, ev.problem_id, lock=True)
    if relation and relation != ev.relation:
        old = ev.relation
        ev.relation = relation
        # a corrected relation is a human decision about this quote
        ev.human_verified, ev.verified_by, ev.verified_at = True, actor, now()
        bump(problem)
        log_history(s, problem.id, "evidence_relation_changed", actor, evidence_id=ev.id, old=old, new=relation)
    if verified is not None and verified != ev.human_verified:
        ev.human_verified = verified
        ev.verified_by, ev.verified_at = (actor, now()) if verified else (None, None)
        log_history(s, problem.id, "evidence_verified" if verified else "evidence_unverified", actor, evidence_id=ev.id)
    if move_to and move_to != ev.problem_id:
        target = get_problem(s, move_to, lock=True)
        if target.status == "archived":
            raise conflict("target_archived", "Das Zielproblem ist archiviert")
        moved, dedup = move_evidence(s, [ev.id], problem, target, actor)
        bump(problem)
        bump(target)
        log_history(s, problem.id, "evidence_moved_out", actor, evidence_id=ev_id, target_id=target.id, merged_duplicate=bool(dedup))
        log_history(s, target.id, "evidence_moved_in", actor, evidence_id=ev_id, source_id=problem.id, merged_duplicate=bool(dedup))
    s.flush()
    return ev


def remove_evidence(s: Session, ev_id: uuid.UUID, actor: uuid.UUID) -> None:
    ev = s.scalar(select(m.ProblemEvidence).where(m.ProblemEvidence.id == ev_id).with_for_update())
    if ev is None:
        raise not_found("Beleg")
    problem = get_problem(s, ev.problem_id, lock=True)
    log_history(s, problem.id, "evidence_removed", actor, evidence_id=ev.id, chunk_id=ev.source_chunk_id, relation=ev.relation)
    s.delete(ev)
    bump(problem)


def delete_problem(s: Session, problem_id: uuid.UUID) -> None:
    p = get_problem(s, problem_id, lock=True)
    if s.scalar(select(func.count()).select_from(m.Initiative).where(m.Initiative.problem_id == p.id)):
        raise conflict("problem_has_initiatives", "Das Problem hat Initiativen und kann nicht gelöscht werden", "Bitte archivieren.")
    s.execute(update(m.Problem).where(m.Problem.merged_into_problem_id == p.id).values(merged_into_problem_id=None))
    s.execute(delete(m.Problem).where(m.Problem.id == p.id))
