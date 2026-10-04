from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from decision_evidence.config import get_settings
from decision_evidence.db import models as m
from decision_evidence.errors import ApiError, conflict, forbidden, not_found
from decision_evidence.identity import repository as identity
from decision_evidence.modules.analysis.service import ai_calls_this_month, provider_label
from decision_evidence.modules.common import Page, Paging, tenant_settings
from decision_evidence.tenancy.context import TenantContext, admin, viewer

router = APIRouter(prefix="/api/v1/workspaces/{tenant_id}", tags=["workspace"])
Role = Literal["owner", "admin", "editor", "viewer"]


class SettingsOut(BaseModel):
    max_file_bytes: int
    max_import_rows: int
    max_running_jobs: int
    ai_monthly_call_budget: int
    ai_calls_this_month: int
    max_ai_context_chunks: int
    allow_self_approval: bool
    evidence_fresh_days: int
    is_demo: bool


class WorkspaceOut(BaseModel):
    tenant_id: uuid.UUID
    slug: str
    name: str
    locale: str
    timezone: str
    role: str
    settings: SettingsOut
    ai: dict[str, Any]
    external_org_id: str | None = None


def _settings_out(s: Any) -> SettingsOut:
    ts = tenant_settings(s)
    return SettingsOut(max_file_bytes=ts.max_file_bytes, max_import_rows=ts.max_import_rows, max_running_jobs=ts.max_running_jobs,
                       ai_monthly_call_budget=ts.ai_monthly_call_budget, ai_calls_this_month=ai_calls_this_month(s), max_ai_context_chunks=ts.max_ai_context_chunks,
                       allow_self_approval=ts.allow_self_approval, evidence_fresh_days=ts.evidence_fresh_days, is_demo=ts.is_demo)


@router.get("", response_model=WorkspaceOut)
def get_workspace(ctx: Annotated[TenantContext, viewer]) -> WorkspaceOut:
    with ctx.session() as s:
        return WorkspaceOut(tenant_id=ctx.tenant_id, slug=ctx.slug, name=ctx.name, locale=ctx.locale, timezone=ctx.timezone, role=ctx.role,
                            settings=_settings_out(s), ai=provider_label(get_settings()))


class SettingsPatch(BaseModel):
    max_file_bytes: int | None = Field(default=None, ge=1024, le=104857600)
    max_import_rows: int | None = Field(default=None, ge=1, le=100000)
    max_running_jobs: int | None = Field(default=None, ge=1, le=50)
    ai_monthly_call_budget: int | None = Field(default=None, ge=0, le=100000)
    max_ai_context_chunks: int | None = Field(default=None, ge=1, le=5000)
    allow_self_approval: bool | None = None
    evidence_fresh_days: int | None = Field(default=None, ge=1, le=3650)


@router.patch("/settings", response_model=SettingsOut)
def patch_settings(ctx: Annotated[TenantContext, admin], body: SettingsPatch) -> SettingsOut:
    with ctx.session() as s:
        ts = tenant_settings(s)
        changes = body.model_dump(exclude_none=True)
        for k, v in changes.items():
            setattr(ts, k, v)
        s.flush()
        ctx.audit(s, "settings.changed", "tenant_settings", ts.id, **changes)
        return _settings_out(s)


class MemberOut(BaseModel):
    user_id: uuid.UUID
    display_name: str | None
    email: str | None
    role: str
    status: str
    last_login_at: datetime | None


@router.get("/members", response_model=list[MemberOut])
def list_members(ctx: Annotated[TenantContext, viewer]) -> list[MemberOut]:
    is_admin = ctx.has_role("admin")
    return [MemberOut(user_id=x["user_id"], display_name=x["display_name"] or x["email"], email=x["email"] if is_admin else None, role=x["role"],
                      status=x["status"], last_login_at=x["last_login_at"] if is_admin else None)
            for x in identity.list_members(ctx.tenant_id) if is_admin or x["status"] == "active"]


class MemberPatch(BaseModel):
    role: Role | None = None
    status: Literal["active", "disabled"] | None = None


def _guard_owner_change(ctx: TenantContext, target_role: str, new_role: str | None) -> None:
    if (target_role == "owner" or new_role == "owner") and ctx.role != "owner":
        raise forbidden("Nur Owner dürfen die Owner-Rolle vergeben oder entziehen.")


@router.patch("/members/{user_id}", response_model=list[MemberOut])
def patch_member(ctx: Annotated[TenantContext, admin], user_id: uuid.UUID, body: MemberPatch) -> list[MemberOut]:
    member = identity.get_member(ctx.tenant_id, user_id)
    if member is None:
        raise not_found("Mitglied")
    _guard_owner_change(ctx, member.role, body.role)
    try:
        identity.update_member(ctx.tenant_id, user_id, role=body.role, status=body.status)
    except identity.LastOwnerError as exc:
        raise conflict("last_owner", "Der letzte Owner kann nicht entfernt oder herabgestuft werden") from exc
    with ctx.session() as s:
        ctx.audit(s, "member.updated", "membership", user_id, role=body.role, status=body.status, previous_role=member.role)
    return list_members(ctx)


@router.delete("/members/{user_id}", response_model=list[MemberOut])
def remove_member(ctx: Annotated[TenantContext, admin], user_id: uuid.UUID) -> list[MemberOut]:
    member = identity.get_member(ctx.tenant_id, user_id)
    if member is None:
        raise not_found("Mitglied")
    _guard_owner_change(ctx, member.role, None)
    try:
        identity.remove_member(ctx.tenant_id, user_id)
    except identity.LastOwnerError as exc:
        raise conflict("last_owner", "Der letzte Owner kann nicht entfernt werden") from exc
    with ctx.session() as s:
        ctx.audit(s, "member.removed", "membership", user_id, previous_role=member.role)
    return list_members(ctx)


class InvitationIn(BaseModel):
    # plain syntax check; special-use domains such as .example/.test must stay usable for demos
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    role: Role = "viewer"


class InvitationCreated(BaseModel):
    id: uuid.UUID
    email: str
    role: str
    expires_at: datetime
    token: str
    invite_path: str


@router.post("/invitations", response_model=InvitationCreated, status_code=201)
def create_invitation(ctx: Annotated[TenantContext, admin], body: InvitationIn) -> InvitationCreated:
    _guard_owner_change(ctx, "viewer", body.role)
    inv_id, token, expires = identity.create_invitation(ctx.tenant_id, str(body.email), body.role, ctx.user_id)
    with ctx.session() as s:
        ctx.audit(s, "invitation.created", "invitation", inv_id, role=body.role, email_domain=str(body.email).split("@")[-1])
    return InvitationCreated(id=inv_id, email=identity.normalize_email(str(body.email)), role=body.role, expires_at=expires, token=token, invite_path=f"/invite?token={token}")


@router.get("/invitations")
def list_invitations(ctx: Annotated[TenantContext, admin]) -> list[dict[str, Any]]:
    return identity.list_invitations(ctx.tenant_id)


@router.delete("/invitations/{invitation_id}", status_code=204)
def revoke_invitation(ctx: Annotated[TenantContext, admin], invitation_id: uuid.UUID) -> Response:
    if not identity.revoke_invitation(ctx.tenant_id, invitation_id):
        raise ApiError(404, "not_found", "Einladung nicht gefunden oder nicht mehr offen")
    with ctx.session() as s:
        ctx.audit(s, "invitation.revoked", "invitation", invitation_id)
    return Response(status_code=204)


class AuditOut(BaseModel):
    id: uuid.UUID
    occurred_at: datetime
    actor_name: str | None
    action: str
    entity_type: str
    entity_id: uuid.UUID | None
    request_id: str | None
    metadata: dict[str, Any]


@router.get("/audit", response_model=Page[AuditOut])
def list_audit(ctx: Annotated[TenantContext, admin], paging: Annotated[Paging, Depends()], action: str | None = None, entity_type: str | None = None) -> Page[AuditOut]:
    with ctx.session() as s:
        cond = []
        if action:
            cond.append(m.AuditEvent.action.startswith(action))
        if entity_type:
            cond.append(m.AuditEvent.entity_type == entity_type)
        total = s.scalar(select(func.count()).select_from(m.AuditEvent).where(*cond)) or 0
        rows = list(s.scalars(select(m.AuditEvent).where(*cond).order_by(m.AuditEvent.occurred_at.desc(), m.AuditEvent.id).limit(paging.limit).offset(paging.offset)))
        names = {k: (v["display_name"] or v["email"]) for k, v in identity.user_names(ctx.tenant_id, {r.actor_user_id for r in rows if r.actor_user_id}).items()}
        return Page[AuditOut](items=[AuditOut(id=r.id, occurred_at=r.occurred_at, actor_name=names.get(r.actor_user_id) if r.actor_user_id else None, action=r.action,
                                              entity_type=r.entity_type, entity_id=r.entity_id, request_id=r.request_id, metadata=r.metadata_) for r in rows],
                              total=total, limit=paging.limit, offset=paging.offset)
