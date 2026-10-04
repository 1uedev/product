"""The small, audited repository layer for global identity data (role de_auth).

Everything in the ``identity`` schema is reachable only through this module. Tenant-related queries always carry an
explicit tenant filter; callers must have authorised the acting user for that tenant first.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import and_, delete, func, select, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from decision_evidence.db import models as m
from decision_evidence.db.engines import get_engine
from decision_evidence.db.session import plain_session
from decision_evidence.identity.crypto import random_token, sha256_hex

ROLES = ("viewer", "editor", "admin", "owner")
ROLE_RANK = {r: i for i, r in enumerate(ROLES)}


class LastOwnerError(Exception):
    pass


@dataclass(frozen=True)
class UserInfo:
    id: uuid.UUID
    email: str | None
    display_name: str | None


@dataclass(frozen=True)
class WorkspaceMembership:
    tenant_id: uuid.UUID
    slug: str
    name: str
    role: str
    locale: str
    timezone: str
    tenant_status: str


@dataclass(frozen=True)
class SessionInfo:
    id: uuid.UUID
    user: UserInfo
    csrf_secret: str
    id_token_enc: bytes | None
    token_hash: str


def _now() -> datetime:
    return datetime.now(UTC)


def auth_session() -> Any:
    return plain_session(get_engine("auth"))


def normalize_email(email: str) -> str:
    return email.strip().lower()


# ------------------------------------------------------------------ users and memberships
def upsert_user(issuer: str, subject: str, email: str | None, display_name: str | None) -> UserInfo:
    with auth_session() as s:
        user = s.scalar(select(m.User).where(m.User.oidc_issuer == issuer, m.User.oidc_subject == subject))
        if user is None:
            user = m.User(oidc_issuer=issuer, oidc_subject=subject, email=email, display_name=display_name)
            s.add(user)
        else:
            user.email = email or user.email
            user.display_name = display_name or user.display_name
        user.last_login_at = _now()
        s.flush()
        return UserInfo(user.id, user.email, user.display_name)


def list_workspaces(user_id: uuid.UUID) -> list[WorkspaceMembership]:
    with auth_session() as s:
        rows = s.execute(
            select(m.Tenant, m.Membership.role)
            .join(m.Membership, m.Membership.tenant_id == m.Tenant.id)
            .where(m.Membership.user_id == user_id, m.Membership.status == "active", m.Tenant.status == "active")
            .order_by(m.Tenant.name)
        ).all()
        return [WorkspaceMembership(t.id, t.slug, t.name, role, t.locale, t.timezone, t.status) for t, role in rows]


def get_workspace_membership(user_id: uuid.UUID, tenant_id: uuid.UUID) -> WorkspaceMembership | None:
    with auth_session() as s:
        row = s.execute(
            select(m.Tenant, m.Membership.role)
            .join(m.Membership, m.Membership.tenant_id == m.Tenant.id)
            .where(m.Membership.user_id == user_id, m.Membership.tenant_id == tenant_id,
                   m.Membership.status == "active", m.Tenant.status == "active")
        ).first()
        if row is None:
            return None
        t, role = row
        return WorkspaceMembership(t.id, t.slug, t.name, role, t.locale, t.timezone, t.status)


def user_names(tenant_id: uuid.UUID, user_ids: set[uuid.UUID]) -> dict[uuid.UUID, dict[str, str | None]]:
    """Display data for users, restricted to members of the given tenant (no global user directory)."""
    if not user_ids:
        return {}
    with auth_session() as s:
        rows = s.execute(
            select(m.User.id, m.User.display_name, m.User.email)
            .join(m.Membership, m.Membership.user_id == m.User.id)
            .where(m.Membership.tenant_id == tenant_id, m.User.id.in_(user_ids))
        ).all()
        return {r.id: {"display_name": r.display_name, "email": r.email} for r in rows}


def is_active_member(tenant_id: uuid.UUID, user_id: uuid.UUID) -> bool:
    return get_workspace_membership(user_id, tenant_id) is not None


def list_members(tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    with auth_session() as s:
        rows = s.execute(
            select(m.Membership, m.User)
            .join(m.User, m.User.id == m.Membership.user_id)
            .where(m.Membership.tenant_id == tenant_id)
            .order_by(m.User.display_name, m.User.email)
        ).all()
        return [
            {"user_id": mem.user_id, "email": u.email, "display_name": u.display_name, "role": mem.role,
             "status": mem.status, "created_at": mem.created_at, "last_login_at": u.last_login_at}
            for mem, u in rows
        ]


def get_member(tenant_id: uuid.UUID, user_id: uuid.UUID) -> m.Membership | None:
    with auth_session() as s:
        row = s.get(m.Membership, (tenant_id, user_id))
        if row:
            s.expunge(row)
        return row


def update_member(tenant_id: uuid.UUID, user_id: uuid.UUID, *, role: str | None, status: str | None) -> None:
    try:
        with auth_session() as s:
            row = s.get(m.Membership, (tenant_id, user_id))
            if row is None:
                raise KeyError("membership")
            if role:
                row.role = role
            if status:
                row.status = status
            s.flush()
    except DBAPIError as exc:
        if "last owner" in str(exc.orig):
            raise LastOwnerError from exc
        raise


def remove_member(tenant_id: uuid.UUID, user_id: uuid.UUID) -> None:
    try:
        with auth_session() as s:
            res = s.execute(delete(m.Membership).where(m.Membership.tenant_id == tenant_id, m.Membership.user_id == user_id))
            if res.rowcount == 0:
                raise KeyError("membership")
    except DBAPIError as exc:
        if "last owner" in str(exc.orig):
            raise LastOwnerError from exc
        raise


# ------------------------------------------------------------------ login attempts and sessions
def create_login_attempt(state: str, nonce: str, verifier_enc: bytes, redirect_after: str, ttl: timedelta = timedelta(minutes=10)) -> None:
    with auth_session() as s:
        s.execute(delete(m.LoginAttempt).where(m.LoginAttempt.expires_at < _now()))
        s.add(m.LoginAttempt(state_hash=sha256_hex(state), nonce=nonce, code_verifier_enc=verifier_enc,
                             redirect_after=redirect_after, expires_at=_now() + ttl))


def consume_login_attempt(state: str) -> m.LoginAttempt | None:
    """Single use: the row is deleted atomically, a replayed callback finds nothing."""
    with auth_session() as s:
        row = s.execute(delete(m.LoginAttempt).where(m.LoginAttempt.state_hash == sha256_hex(state)).returning(m.LoginAttempt)).scalar()
        if row is None or row.expires_at < _now():
            return None
        return row


def create_session(user_id: uuid.UUID, id_token_enc: bytes | None, ttl_hours: int) -> tuple[str, str]:
    """Returns (opaque session token for the cookie, csrf secret). Only the token's SHA-256 is stored."""
    token, csrf = random_token(32), random_token(24)
    with auth_session() as s:
        s.add(m.SessionRow(user_id=user_id, token_hash=sha256_hex(token), csrf_secret=csrf, id_token_enc=id_token_enc,
                           expires_at=_now() + timedelta(hours=ttl_hours)))
        s.execute(delete(m.SessionRow).where(m.SessionRow.expires_at < _now() - timedelta(days=1)))
    return token, csrf


def load_session(token: str) -> SessionInfo | None:
    th = sha256_hex(token)
    with auth_session() as s:
        row = s.execute(
            select(m.SessionRow, m.User).join(m.User, m.User.id == m.SessionRow.user_id)
            .where(m.SessionRow.token_hash == th, m.SessionRow.revoked_at.is_(None), m.SessionRow.expires_at > _now())
        ).first()
        if row is None:
            return None
        sess, user = row
        if _now() - sess.last_seen_at > timedelta(minutes=1):
            sess.last_seen_at = _now()
        return SessionInfo(sess.id, UserInfo(user.id, user.email, user.display_name), sess.csrf_secret, sess.id_token_enc, th)


def revoke_session(token_hash: str) -> None:
    with auth_session() as s:
        s.execute(update(m.SessionRow).where(m.SessionRow.token_hash == token_hash).values(revoked_at=_now()))


# ------------------------------------------------------------------ invitations
INVITE_TTL = timedelta(days=7)


def create_invitation(tenant_id: uuid.UUID, email: str, role: str, invited_by: uuid.UUID) -> tuple[uuid.UUID, str, datetime]:
    token = random_token(32)
    expires = _now() + INVITE_TTL
    with auth_session() as s:
        inv = m.Invitation(tenant_id=tenant_id, normalized_email=normalize_email(email), intended_role=role,
                           token_hash=sha256_hex(token), expires_at=expires, invited_by=invited_by)
        s.add(inv)
        s.flush()
        return inv.id, token, expires


def list_invitations(tenant_id: uuid.UUID) -> list[dict[str, Any]]:
    with auth_session() as s:
        rows = s.scalars(select(m.Invitation).where(m.Invitation.tenant_id == tenant_id).order_by(m.Invitation.created_at.desc()).limit(100))
        now = _now()
        out = []
        for i in rows:
            state = "accepted" if i.accepted_at else "revoked" if i.revoked_at else "expired" if i.expires_at < now else "open"
            out.append({"id": i.id, "email": i.normalized_email, "role": i.intended_role, "state": state,
                        "expires_at": i.expires_at, "created_at": i.created_at})
        return out


def revoke_invitation(tenant_id: uuid.UUID, invitation_id: uuid.UUID) -> bool:
    with auth_session() as s:
        res = s.execute(update(m.Invitation).where(m.Invitation.id == invitation_id, m.Invitation.tenant_id == tenant_id,
                                                   m.Invitation.accepted_at.is_(None), m.Invitation.revoked_at.is_(None))
                        .values(revoked_at=_now()))
        return res.rowcount > 0


def describe_invitation(token: str) -> dict[str, Any] | None:
    with auth_session() as s:
        row = s.execute(select(m.Invitation, m.Tenant.name).join(m.Tenant, m.Tenant.id == m.Invitation.tenant_id)
                        .where(m.Invitation.token_hash == sha256_hex(token))).first()
        if row is None:
            return None
        inv, tname = row
        usable = inv.accepted_at is None and inv.revoked_at is None and inv.expires_at > _now()
        return {"tenant_name": tname, "role": inv.intended_role, "email": inv.normalized_email, "usable": usable}


class InvitationError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def accept_invitation(token: str, user_id: uuid.UUID, user_email: str | None, email_verified: bool = True) -> uuid.UUID:
    """One-time use. The invitation is bound to the invited e-mail address; the token alone is not enough."""
    with auth_session() as s:
        inv = s.scalar(select(m.Invitation).where(m.Invitation.token_hash == sha256_hex(token)).with_for_update())
        if inv is None or inv.revoked_at is not None:
            raise InvitationError("invalid")
        if inv.accepted_at is not None:
            raise InvitationError("used")
        if inv.expires_at < _now():
            raise InvitationError("expired")
        if not user_email or normalize_email(user_email) != inv.normalized_email:
            raise InvitationError("email_mismatch")
        existing = s.get(m.Membership, (inv.tenant_id, user_id))
        if existing is None:
            s.add(m.Membership(tenant_id=inv.tenant_id, user_id=user_id, role=inv.intended_role))
        elif existing.status != "active" or ROLE_RANK[inv.intended_role] > ROLE_RANK[existing.role]:
            existing.status = "active"
            existing.role = inv.intended_role if ROLE_RANK[inv.intended_role] > ROLE_RANK[existing.role] else existing.role
        inv.accepted_at = _now()
        inv.accepted_by = user_id
        return inv.tenant_id


def count_active_owners(tenant_id: uuid.UUID) -> int:
    with auth_session() as s:
        return s.scalar(select(func.count()).select_from(m.Membership).where(
            and_(m.Membership.tenant_id == tenant_id, m.Membership.role == "owner", m.Membership.status == "active"))) or 0
