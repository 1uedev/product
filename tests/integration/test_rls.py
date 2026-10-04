"""Tenant isolation enforced by PostgreSQL itself, using the restricted runtime roles."""

from __future__ import annotations

import datetime as dt
import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError, ProgrammingError
from sqlalchemy.orm import Session

from decision_evidence.db import models as m
from decision_evidence.db.engines import get_engine
from decision_evidence.db.session import plain_session, tenant_session


def test_tenant_a_cannot_read_tenant_b(seeded, app_session) -> None:  # type: ignore[no-untyped-def]
    a, b = seeded["a"], seeded["b"]
    with app_session(a["tenant"].id) as s:
        names = {c.name for c in s.scalars(select(m.CustomerAccount))}
        assert names == {"Kunde a"}
        # manipulated ids: asking for B's rows by id returns nothing
        assert s.get(m.CustomerAccount, b["customer"]) is None
        assert s.get(m.SourceChunk, b["chunk"]) is None
        assert s.scalars(select(m.Problem).where(m.Problem.id == b["problem"])).first() is None
        assert s.execute(text("select count(*) from app.source_chunks where id = :i"), {"i": b["chunk"]}).scalar() == 0


def test_full_text_search_is_tenant_scoped(seeded, app_session) -> None:  # type: ignore[no-untyped-def]
    a, b = seeded["a"], seeded["b"]
    with app_session(a["tenant"].id) as s:
        hits = s.execute(text("select id from app.source_chunks where search_vector @@ plainto_tsquery('german', 'Export')")).scalars().all()
        assert hits == [a["chunk"]]
        assert b["chunk"] not in hits


def test_missing_tenant_context_returns_nothing_and_blocks_writes(seeded) -> None:  # type: ignore[no-untyped-def]
    with Session(get_engine("app")) as s:
        assert s.execute(text("select count(*) from app.customer_accounts")).scalar() == 0
        assert s.execute(text("select count(*) from app.audit_events")).scalar() == 0
        with pytest.raises(ProgrammingError):
            s.add(m.Problem(tenant_id=seeded["a"]["tenant"].id, title="no context"))
            s.flush()
        s.rollback()


def test_cannot_write_into_foreign_tenant_even_with_own_context(seeded, app_session) -> None:  # type: ignore[no-untyped-def]
    a, b = seeded["a"], seeded["b"]
    with pytest.raises(ProgrammingError), app_session(a["tenant"].id) as s:
        s.add(m.Problem(tenant_id=b["tenant"].id, title="forged"))
        s.flush()


def test_cannot_update_or_delete_foreign_rows(seeded, app_session) -> None:  # type: ignore[no-untyped-def]
    a, b = seeded["a"], seeded["b"]
    with app_session(a["tenant"].id) as s:
        res = s.execute(text("update app.problems set title='hacked' where id = :i"), {"i": b["problem"]})
        assert res.rowcount == 0
        res = s.execute(text("delete from app.customer_accounts where id = :i"), {"i": b["customer"]})
        assert res.rowcount == 0
    with app_session(b["tenant"].id) as s:
        assert s.get(m.Problem, b["problem"]).title == "Problem b"  # type: ignore[union-attr]


def test_cross_tenant_foreign_key_fails_in_database(seeded, app_session) -> None:  # type: ignore[no-untyped-def]
    """Even where RLS would allow the row (own tenant_id), a reference to another tenant's object is rejected by the FK."""
    a, b = seeded["a"], seeded["b"]
    with pytest.raises(IntegrityError), app_session(a["tenant"].id) as s:
        s.add(m.Opportunity(external_id="O-x", customer_account_id=b["customer"], name="x", stage="open"))
        s.flush()
    with pytest.raises(IntegrityError), app_session(a["tenant"].id) as s:
        s.add(m.Initiative(problem_id=b["problem"], title="x"))
        s.flush()
    with pytest.raises(IntegrityError), app_session(a["tenant"].id) as s:
        # evidence pointing to a chunk/feedback of tenant B
        s.add(m.ProblemEvidence(problem_id=a["problem"], feedback_item_id=b["feedback"], source_chunk_id=b["chunk"],
                                relation="supports", extracted_quote="x"))
        s.flush()


def test_evidence_chunk_must_belong_to_feedback_item(seeded, app_session) -> None:  # type: ignore[no-untyped-def]
    a = seeded["a"]
    with app_session(a["tenant"].id) as s:
        sr = m.SourceRecord(source_kind="note_text", origin="synthetic", title="n2", raw_text="anderes",
                            content_hash=uuid.uuid4().hex * 2)
        s.add(sr)
        s.flush()
        other_chunk = m.SourceChunk(source_record_id=sr.id, ordinal=0, text="anderes")
        s.add(other_chunk)
        s.flush()
        other_chunk_id = other_chunk.id
    with pytest.raises((IntegrityError, DBAPIError)), app_session(a["tenant"].id) as s:
        s.add(m.ProblemEvidence(problem_id=a["problem"], feedback_item_id=a["feedback"], source_chunk_id=other_chunk_id,
                                relation="supports", extracted_quote="anderes"))
        s.flush()


def test_pool_reuse_leaves_no_tenant_context(seeded, app_session) -> None:  # type: ignore[no-untyped-def]
    a = seeded["a"]
    engine = get_engine("app")
    seen: set[int] = set()
    for _ in range(6):
        with app_session(a["tenant"].id) as s:
            assert s.scalar(text("select count(*) from app.customer_accounts")) == 1
            seen.add(id(s.connection().connection.dbapi_connection))
        with Session(engine) as raw:
            assert raw.execute(text("select coalesce(current_setting('app.tenant_id', true), '')")).scalar() in ("", None)
            assert raw.execute(text("select count(*) from app.customer_accounts")).scalar() == 0
            seen.add(id(raw.connection().connection.dbapi_connection))
    assert len(seen) < 12  # connections were actually reused


def test_tenant_context_survives_commit_inside_session(seeded, app_session) -> None:  # type: ignore[no-untyped-def]
    a = seeded["a"]
    with app_session(a["tenant"].id) as s:
        s.commit()  # new transaction starts; context must be re-applied
        assert s.scalar(text("select count(*) from app.customer_accounts")) == 1


def test_audit_events_are_append_only(seeded, app_session) -> None:  # type: ignore[no-untyped-def]
    a = seeded["a"]
    with app_session(a["tenant"].id) as s:
        ev = m.AuditEvent(action="test.action", entity_type="problem", entity_id=a["problem"], metadata_={"k": 1})
        s.add(ev)
        s.flush()
        eid = ev.id
    with pytest.raises(DBAPIError), app_session(a["tenant"].id) as s:
        s.execute(text("update app.audit_events set action='x' where id=:i"), {"i": eid})
    with pytest.raises(DBAPIError), app_session(a["tenant"].id) as s:
        s.execute(text("delete from app.audit_events where id=:i"), {"i": eid})


def test_runtime_roles_cannot_cross_schema_boundaries(seeded) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ProgrammingError), Session(get_engine("app")) as s:
        s.execute(text("select * from identity.users limit 1"))
    with pytest.raises(ProgrammingError), Session(get_engine("worker")) as s:
        s.execute(text("select * from identity.sessions limit 1"))
    with pytest.raises(ProgrammingError), Session(get_engine("auth")) as s:
        s.execute(text("select * from app.problems limit 1"))
    with pytest.raises(ProgrammingError), Session(get_engine("outbox")) as s:
        s.execute(text("select * from app.jobs limit 1"))
    with pytest.raises(ProgrammingError), Session(get_engine("outbox")) as s:
        s.execute(text("select * from app.source_chunks limit 1"))


def _make_job(s: Session, tenant_id: uuid.UUID, key: str = "k1") -> uuid.UUID:
    job = m.Job(kind="analyze_feedback", idempotency_key=key, payload={"scope": "x"}, correlation_id="req-1")
    s.add(job)
    s.flush()
    s.add(m.Outbox(job_id=job.id, correlation_id="req-1"))
    s.flush()
    return job.id


def test_outbox_publisher_sees_only_mediation_metadata(seeded, app_session) -> None:  # type: ignore[no-untyped-def]
    a, b = seeded["a"], seeded["b"]
    with app_session(a["tenant"].id) as s:
        ja = _make_job(s, a["tenant"].id)
    with app_session(b["tenant"].id) as s:
        jb = _make_job(s, b["tenant"].id)
    with plain_session(get_engine("outbox")) as s:
        rows = s.execute(text(
            "select tenant_id, job_id from infra.outbox where published_at is null and job_id in (:a,:b)"), {"a": ja, "b": jb}).all()
        assert {r[1] for r in rows} == {ja, jb}  # publisher reads across tenants...
        s.execute(text("update infra.outbox set published_at=now(), attempts=attempts+1 where job_id=:j"), {"j": ja})
    with pytest.raises(ProgrammingError), plain_session(get_engine("outbox")) as s:
        s.execute(text("select payload from app.jobs"))   # ...but never business data
    with pytest.raises(ProgrammingError), plain_session(get_engine("outbox")) as s:
        s.execute(text("update infra.outbox set tenant_id = tenant_id, job_id = job_id"))  # no column grants beyond marking
    with app_session(a["tenant"].id) as s:
        assert s.execute(text("select count(*) from infra.outbox where job_id=:j"), {"j": jb}).scalar() == 0


def test_app_role_cannot_forge_outbox_for_other_tenant(seeded, app_session) -> None:  # type: ignore[no-untyped-def]
    a, b = seeded["a"], seeded["b"]
    with app_session(b["tenant"].id) as s:
        jb = _make_job(s, b["tenant"].id, "forge")
    with pytest.raises(ProgrammingError), app_session(a["tenant"].id) as s:
        s.add(m.Outbox(tenant_id=b["tenant"].id, job_id=jb))
        s.flush()


def test_job_idempotency_key_is_unique_per_tenant_and_kind(seeded, app_session) -> None:  # type: ignore[no-untyped-def]
    a, b = seeded["a"], seeded["b"]
    with app_session(a["tenant"].id) as s:
        _make_job(s, a["tenant"].id, "same")
    with pytest.raises(IntegrityError), app_session(a["tenant"].id) as s:
        _make_job(s, a["tenant"].id, "same")
    with app_session(b["tenant"].id) as s:
        _make_job(s, b["tenant"].id, "same")  # other tenant: fine


def test_recovery_functions_return_only_stale_identifiers(seeded, app_session) -> None:  # type: ignore[no-untyped-def]
    a = seeded["a"]
    with app_session(a["tenant"].id) as s:
        job = m.Job(kind="analyze_feedback", idempotency_key="stale-1", status="running",
                    lease_expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=5), claim_token=uuid.uuid4(), attempts=1)
        s.add(job)
        s.flush()
        stale_id = job.id
        fresh = m.Job(kind="analyze_feedback", idempotency_key="fresh-1", status="running",
                      lease_expires_at=dt.datetime.now(dt.UTC) + dt.timedelta(minutes=5), claim_token=uuid.uuid4(), attempts=1)
        s.add(fresh)
        s.flush()
        fresh_id = fresh.id
    with plain_session(get_engine("worker")) as s:
        rows = s.execute(text("select tenant_id, job_id from infra.find_stale_jobs(500)")).all()
    ids = {r[1] for r in rows}
    assert stale_id in ids and fresh_id not in ids
    with plain_session(get_engine("worker")) as s:  # no blanket read: without tenant context RLS yields zero rows
        assert s.execute(text("select count(*) from app.jobs")).scalar() == 0


def test_decision_documents_are_immutable_after_approval(seeded, app_session) -> None:  # type: ignore[no-untyped-def]
    a = seeded["a"]
    owner = a["tenant"].owner_id
    with app_session(a["tenant"].id) as s:
        init = m.Initiative(problem_id=a["problem"], title="Init")
        s.add(init)
        s.flush()
        doc = m.DecisionDocument(initiative_id=init.id, revision=1, options=[{"name": "A"}, {"name": "B"}],
                                 snapshot_taken_at=dt.datetime.now(dt.UTC), state="in_review")
        s.add(doc)
        s.flush()
        doc_id, init_id = doc.id, init.id
        ev = m.DecisionEvidence(decision_document_id=doc.id, source_chunk_id=a["chunk"], relation="supports", quote="x")
        s.add(ev)
    with app_session(a["tenant"].id) as s:
        d = s.get(m.DecisionDocument, doc_id)
        d.state, d.approved_by, d.approved_at = "approved", owner, dt.datetime.now(dt.UTC)  # type: ignore[union-attr]
    with pytest.raises(DBAPIError), app_session(a["tenant"].id) as s:
        s.execute(text("update app.decision_documents set recommendation_text='changed' where id=:i"), {"i": doc_id})
    with pytest.raises(DBAPIError), app_session(a["tenant"].id) as s:
        s.execute(text("delete from app.decision_documents where id=:i"), {"i": doc_id})
    with pytest.raises(DBAPIError), app_session(a["tenant"].id) as s:
        s.execute(text("delete from app.decision_evidence where decision_document_id=:i"), {"i": doc_id})
    with pytest.raises(DBAPIError), app_session(a["tenant"].id) as s:
        s.execute(text("update app.decision_documents set state='draft' where id=:i"), {"i": doc_id})
    # the one allowed transition: approved -> superseded
    with app_session(a["tenant"].id) as s:
        s.execute(text("update app.decision_documents set state='superseded' where id=:i"), {"i": doc_id})
    # a source chunk referenced by a decision cannot be deleted
    with pytest.raises(IntegrityError), app_session(a["tenant"].id) as s:
        s.execute(text("delete from app.source_chunks where id=:i"), {"i": a["chunk"]})
    assert init_id


def test_assumption_observed_requires_source_and_money_checks(seeded, app_session) -> None:  # type: ignore[no-untyped-def]
    a = seeded["a"]
    with pytest.raises(IntegrityError), app_session(a["tenant"].id) as s:
        init = m.Initiative(problem_id=a["problem"], title="I")
        s.add(init)
        s.flush()
        s.add(m.InitiativeAssumption(initiative_id=init.id, statement="x", kind="observed"))
        s.flush()
    with pytest.raises(IntegrityError), app_session(a["tenant"].id) as s:  # value without currency
        s.add(m.CustomerAccount(external_id="C-2", name="x", commercial_value=10, value_basis="arr"))
        s.flush()
    with pytest.raises(IntegrityError), app_session(a["tenant"].id) as s:  # unknown basis must not carry a number (no fake zero)
        s.add(m.CustomerAccount(external_id="C-3", name="x", commercial_value=0, value_basis="unknown", currency="EUR"))
        s.flush()


def test_scoring_weights_constraint(seeded, app_session) -> None:  # type: ignore[no-untyped-def]
    a = seeded["a"]
    with app_session(a["tenant"].id) as s:
        s.add(m.ScoringPolicy(name="ok", weights={"customer_reach": 0.5, "low_effort": 0.5}))
    with pytest.raises(IntegrityError), app_session(a["tenant"].id) as s:
        s.add(m.ScoringPolicy(name="sum", weights={"customer_reach": 0.5, "low_effort": 0.4}))
        s.flush()
    with pytest.raises(IntegrityError), app_session(a["tenant"].id) as s:
        s.add(m.ScoringPolicy(name="criterion", weights={"magic": 1.0}))
        s.flush()


def test_last_owner_cannot_be_removed(make_tenant, make_user) -> None:  # type: ignore[no-untyped-def]
    t = make_tenant("Owner")
    with pytest.raises(DBAPIError), plain_session(get_engine("auth")) as s:
        s.execute(text("update identity.memberships set role='viewer' where tenant_id=:t and user_id=:u"),
                  {"t": t.id, "u": t.owner_id})
    with pytest.raises(DBAPIError), plain_session(get_engine("auth")) as s:
        s.execute(text("delete from identity.memberships where tenant_id=:t and user_id=:u"), {"t": t.id, "u": t.owner_id})
    second = make_user("second")
    with plain_session(get_engine("auth")) as s:
        s.add(m.Membership(tenant_id=t.id, user_id=second, role="owner"))
    with plain_session(get_engine("auth")) as s:  # now demoting one is fine
        s.execute(text("update identity.memberships set role='admin' where tenant_id=:t and user_id=:u"), {"t": t.id, "u": second})
