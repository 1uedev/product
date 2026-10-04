from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from decision_evidence.config import get_settings
from decision_evidence.db import models as m
from decision_evidence.db.engines import get_engine
from decision_evidence.db.session import plain_session, tenant_session


@pytest.fixture(scope="session", autouse=True)
def _database_available() -> None:
    try:
        with get_engine("migrator").connect() as conn:
            conn.execute(text("select 1"))
            version = conn.execute(text("select version_num from infra.alembic_version")).scalar()
    except (OperationalError, Exception) as exc:  # noqa: BLE001
        pytest.skip(f"PostgreSQL with migrations not available: {exc.__class__.__name__}", allow_module_level=False)
    assert version, "alembic version missing"
    get_settings()


@dataclass
class TenantFixture:
    id: uuid.UUID
    slug: str
    owner_id: uuid.UUID


@pytest.fixture
def make_user() -> Callable[..., uuid.UUID]:
    def _make(label: str = "user") -> uuid.UUID:
        with plain_session(get_engine("auth")) as s:
            u = m.User(oidc_issuer="http://issuer.test", oidc_subject=f"{label}-{uuid.uuid4()}", email=f"{label}@example.test",
                       display_name=label)
            s.add(u)
            s.flush()
            return u.id
    return _make


@pytest.fixture
def make_tenant(make_user: Callable[..., uuid.UUID]) -> Callable[..., TenantFixture]:
    """Creates a tenant with an owner and settings, using the bootstrap (migrator) role like the real bootstrap step."""

    def _make(name: str = "Tenant") -> TenantFixture:
        owner = make_user("owner")
        tid = uuid.uuid4()
        slug = f"t-{tid.hex[:12]}"
        with plain_session(get_engine("migrator")) as s:
            s.add(m.Tenant(id=tid, slug=slug, name=f"{name} {slug}"))
            s.flush()
            s.add(m.Membership(tenant_id=tid, user_id=owner, role="owner"))
        with tenant_session(get_engine("migrator"), tid) as s:
            s.add(m.TenantSettings(tenant_id=tid))
        return TenantFixture(tid, slug, owner)

    return _make


@pytest.fixture
def app_session() -> Callable[[uuid.UUID], object]:
    def _open(tenant_id: uuid.UUID):  # type: ignore[no-untyped-def]
        return tenant_session(get_engine("app"), tenant_id)
    return _open


@pytest.fixture
def seeded(make_tenant, app_session) -> Iterator[dict]:  # type: ignore[no-untyped-def]
    """Two tenants, each with a customer, a source record + chunk, a feedback item and a problem."""
    out: dict = {}
    for key in ("a", "b"):
        t = make_tenant(key.upper())
        with app_session(t.id) as s:
            cust = m.CustomerAccount(external_id="C-1", name=f"Kunde {key}", segment="smb")
            s.add(cust)
            s.flush()
            sr = m.SourceRecord(source_kind="note_text", origin="synthetic", title="n", raw_text="Der Export ist zu langsam",
                                content_hash=uuid.uuid4().hex + uuid.uuid4().hex)
            s.add(sr)
            s.flush()
            chunk = m.SourceChunk(source_record_id=sr.id, ordinal=0, text="Der Export ist zu langsam", locator={"paragraph": 1})
            fb = m.FeedbackItem(source_record_id=sr.id, customer_account_id=cust.id, channel="call",
                                occurred_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
                                body="Der Export ist zu langsam", language="de")
            prob = m.Problem(title=f"Problem {key}")
            s.add_all([chunk, fb, prob])
            s.flush()
            out[key] = {"tenant": t, "customer": cust.id, "record": sr.id, "chunk": chunk.id, "feedback": fb.id, "problem": prob.id}
    yield out
