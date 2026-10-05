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
    except (OperationalError, Exception) as exc:
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


# --------------------------------------------------------------------------- API-level fixtures
import re

from fastapi.testclient import TestClient

from decision_evidence.identity import repository as identity_repo
from decision_evidence.jobs import handlers as _handlers  # noqa: F401
from decision_evidence.jobs.runner import execute_job
from decision_evidence.storage.memory import MemoryStorage
from decision_evidence.storage.s3 import set_storage_override


@pytest.fixture(autouse=True)
def memory_storage() -> Iterator[MemoryStorage]:
    storage = MemoryStorage()
    set_storage_override(storage)
    yield storage
    set_storage_override(None)


class Api:
    """Thin client for one signed-in user. Sessions are created through the identity repository (the real OIDC round trip
    is covered by the browser end-to-end tests); everything else goes through HTTP."""

    def __init__(self, user_id: uuid.UUID, tenant_id: uuid.UUID | None = None) -> None:
        from decision_evidence.main import create_app

        settings = get_settings()
        self.client = TestClient(create_app(), base_url="http://localhost:8480", raise_server_exceptions=False)
        token, self.csrf = identity_repo.create_session(user_id, None, 8)
        self.client.cookies.set(settings.effective_cookie_name, token)
        self.user_id, self.tenant_id = user_id, tenant_id

    def url(self, path: str) -> str:
        return f"/api/v1/workspaces/{self.tenant_id}{path}"

    def _h(self, extra: dict | None = None) -> dict:
        return {"X-CSRF-Token": self.csrf, "Origin": "http://localhost:8480", **(extra or {})}

    def get(self, path: str, **kw):  # type: ignore[no-untyped-def]
        return self.client.get(self.url(path), **kw)

    def post(self, path: str, json=None, **kw):  # type: ignore[no-untyped-def]
        return self.client.post(self.url(path), json=json, headers=self._h(), **kw)

    def patch(self, path: str, json=None, version: int | None = None, **kw):  # type: ignore[no-untyped-def]
        headers = self._h({"If-Match": f'"{version}"'} if version is not None else None)
        return self.client.patch(self.url(path), json=json, headers=headers, **kw)

    def delete(self, path: str, **kw):  # type: ignore[no-untyped-def]
        return self.client.delete(self.url(path), headers=self._h(), **kw)

    def upload(self, kind: str, name: str, content: str | bytes, ctype: str = "text/csv", synthetic: bool = False):  # type: ignore[no-untyped-def]
        data = content.encode() if isinstance(content, str) else content
        return self.client.post(self.url("/imports"), headers=self._h(), data={"kind": kind, "synthetic": str(synthetic).lower()},
                                files={"file": (name, data, ctype)})


def run_jobs(tenant_id: uuid.UUID, limit: int = 10) -> list[tuple[str, str]]:
    """Simulates publisher + worker deterministically: executes every queued job of the tenant once."""
    done = []
    with tenant_session(get_engine("worker"), tenant_id) as s:
        ids = [(j.id, j.kind) for j in s.scalars(__import__("sqlalchemy").select(m.Job).where(m.Job.status == "queued").order_by(m.Job.created_at).limit(limit))]
    for jid, kind in ids:
        done.append((kind, execute_job(tenant_id, jid)))
    return done


@pytest.fixture
def workspace(make_tenant, make_user):  # type: ignore[no-untyped-def]
    """A tenant with one user per role plus an outsider of another tenant."""
    t = make_tenant("Workspace")
    users = {role: make_user(role) for role in ("admin", "editor", "viewer")}
    with plain_session(get_engine("auth")) as s:
        for role, uid in users.items():
            s.add(m.Membership(tenant_id=t.id, user_id=uid, role=role))
    other = make_tenant("Other")
    return {
        "tenant": t, "other": other,
        "owner": Api(t.owner_id, t.id), "admin": Api(users["admin"], t.id), "editor": Api(users["editor"], t.id), "viewer": Api(users["viewer"], t.id),
        "outsider": Api(other.owner_id, t.id),            # signed in, but a member of the OTHER tenant only
        "other_owner": Api(other.owner_id, other.id),
    }


def import_demo(api: Api, profile: str = "small") -> None:
    from decision_evidence.tools import demo_data

    ds = demo_data.generate(profile)
    for kind, name, content in (("customers", "kunden.csv", ds.customers_csv), ("opportunities", "opps.csv", ds.opportunities_csv),
                                ("feedback", "feedback.csv", ds.feedback_csv)):
        r = api.upload(kind, name, content, synthetic=True)
        assert r.status_code == 201, r.text
        batch = r.json()
        r = api.client.put(api.url(f"/imports/{batch['id']}/settings"), json={}, headers=api._h())
        assert r.status_code == 200, r.text
        assert not r.json()["preview"]["blocking"], r.json()["preview"]
        r = api.post(f"/imports/{batch['id']}/commit")
        assert r.status_code == 202, r.text
        outcome = run_jobs(api.tenant_id)  # type: ignore[arg-type]
        assert outcome == [("import_commit", "succeeded")], outcome


_ = re
