"""Request/task scoped tenant context. The database enforces isolation; this only supplies defaults."""

from __future__ import annotations

from contextvars import ContextVar
from uuid import UUID

current_tenant: ContextVar[UUID | None] = ContextVar("current_tenant", default=None)
current_request_id: ContextVar[str | None] = ContextVar("current_request_id", default=None)


def tenant_default() -> UUID:
    """Column default for tenant_id. Fails loudly when no tenant context is active."""
    tenant = current_tenant.get()
    if tenant is None:
        raise RuntimeError("no tenant context active")
    return tenant
