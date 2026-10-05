"""Tenant-scoped sessions.

The tenant id is applied with a parameterised ``set_config('app.tenant_id', :id, true)`` at the start of *every*
transaction of the session (``after_begin`` hook), so a mid-request commit can never continue without context.
The setting is transaction-local: returning a connection to the pool leaves no tenant behind.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from sqlalchemy import Engine, event
from sqlalchemy.orm import Session, SessionTransaction

from decision_evidence.db.context import current_tenant

_SET_TENANT = "SELECT set_config('app.tenant_id', %s, true)"


@event.listens_for(Session, "after_begin")
def _apply_tenant(session: Session, transaction: SessionTransaction, connection) -> None:  # type: ignore[no-untyped-def]
    tenant_id = session.info.get("tenant_id")
    if tenant_id is not None:
        connection.exec_driver_sql(_SET_TENANT, (str(tenant_id),))


@contextmanager
def tenant_session(engine: Engine, tenant_id: UUID) -> Iterator[Session]:
    """Unit of work for one tenant. Commits on success, rolls back on error."""
    if not isinstance(tenant_id, UUID):
        raise TypeError("tenant_id must be a UUID")
    session = Session(engine, expire_on_commit=False)
    session.info["tenant_id"] = tenant_id
    token = current_tenant.set(tenant_id)
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        current_tenant.reset(token)
        session.close()


@contextmanager
def plain_session(engine: Engine) -> Iterator[Session]:
    """Session without tenant context (identity/auth repositories, outbox publisher, bootstrap)."""
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
