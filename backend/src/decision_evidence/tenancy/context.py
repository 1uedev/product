"""Tenant context and role checks. Authorisation happens here, on the server, for every tenant route."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Any

from fastapi import Depends
from sqlalchemy.orm import Session

from decision_evidence.db import models as m
from decision_evidence.db.context import current_request_id
from decision_evidence.db.engines import get_engine
from decision_evidence.db.session import tenant_session
from decision_evidence.errors import ApiError, not_found
from decision_evidence.identity import repository as repo
from decision_evidence.identity.deps import current_session

ROLE_RANK = repo.ROLE_RANK


@dataclass(frozen=True)
class TenantContext:
    tenant_id: uuid.UUID
    slug: str
    name: str
    locale: str
    timezone: str
    role: str
    user_id: uuid.UUID
    user_email: str | None
    user_name: str | None

    def session(self) -> AbstractContextManager[Session]:
        return tenant_session(get_engine("app"), self.tenant_id)

    def has_role(self, minimum: str) -> bool:
        return ROLE_RANK[self.role] >= ROLE_RANK[minimum]

    def audit(self, s: Session, action: str, entity_type: str, entity_id: uuid.UUID | None = None, **metadata: Any) -> None:
        add_audit(s, self.user_id, action, entity_type, entity_id, metadata)


def add_audit(s: Session, actor: uuid.UUID | None, action: str, entity_type: str, entity_id: uuid.UUID | None,
              metadata: dict[str, Any] | None = None) -> None:
    s.add(m.AuditEvent(actor_user_id=actor, action=action, entity_type=entity_type, entity_id=entity_id,
                       request_id=current_request_id.get(), metadata_=metadata or {}))


def _resolve(tenant_id: uuid.UUID, session_info: repo.SessionInfo) -> TenantContext:
    membership = repo.get_workspace_membership(session_info.user.id, tenant_id)
    if membership is None:
        # same answer for "does not exist" and "not a member": UUIDs are identifiers, not protection
        raise not_found("Workspace")
    return TenantContext(
        tenant_id=membership.tenant_id, slug=membership.slug, name=membership.name, locale=membership.locale,
        timezone=membership.timezone, role=membership.role, user_id=session_info.user.id,
        user_email=session_info.user.email, user_name=session_info.user.display_name,
    )


def require_role(minimum: str) -> Callable[..., TenantContext]:
    """Dependency factory: membership check + minimum role for the workspace in the URL."""

    def dependency(tenant_id: uuid.UUID, session_info: repo.SessionInfo = Depends(current_session)) -> TenantContext:
        ctx = _resolve(tenant_id, session_info)
        if ROLE_RANK[ctx.role] < ROLE_RANK[minimum]:
            raise ApiError(403, "forbidden", "Keine Berechtigung", f"Dafür ist mindestens die Rolle „{minimum}“ nötig.")
        return ctx

    return dependency


viewer = Depends(require_role("viewer"))
editor = Depends(require_role("editor"))
admin = Depends(require_role("admin"))
owner = Depends(require_role("owner"))
