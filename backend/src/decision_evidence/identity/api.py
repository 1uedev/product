from __future__ import annotations

from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel

from decision_evidence.config import get_settings
from decision_evidence.errors import ApiError
from decision_evidence.identity import crypto, repository as repo
from decision_evidence.identity.deps import current_session
from decision_evidence.identity.oidc import OidcClient, OidcError, new_pkce_pair, safe_redirect_path
from decision_evidence.observability.logging import get_logger

log = get_logger(__name__)
router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_client: OidcClient | None = None


def oidc() -> OidcClient:
    global _client
    if _client is None:
        _client = OidcClient()
    return _client


@router.get("/login", include_in_schema=True, status_code=302)
def login(next: str = "/") -> RedirectResponse:
    client = oidc()
    state, nonce = crypto.random_token(24), crypto.random_token(24)
    verifier, challenge = new_pkce_pair()
    try:
        url = client.authorization_url(state=state, nonce=nonce, code_challenge=challenge)
    except OidcError as exc:
        raise ApiError(503, "idp_unavailable", "Anmeldedienst nicht erreichbar", str(exc)) from exc
    repo.create_login_attempt(state, nonce, crypto.encrypt(verifier), safe_redirect_path(next))
    return RedirectResponse(url, status_code=302)


@router.get("/callback", status_code=302)
def callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None) -> RedirectResponse:
    settings = get_settings()
    if error or not code or not state:
        return _login_failed("idp_error")
    attempt = repo.consume_login_attempt(state)
    if attempt is None:
        return _login_failed("state_invalid")
    verifier = crypto.decrypt(attempt.code_verifier_enc)
    if verifier is None:
        return _login_failed("state_invalid")
    client = oidc()
    try:
        tokens = client.exchange_code(code, verifier)
        id_token = tokens.get("id_token")
        if not id_token:
            raise OidcError("Kein ID-Token erhalten.")
        claims = client.verify_id_token(id_token, nonce=attempt.nonce)
    except OidcError as exc:
        log.warning("login failed", extra={"reason": str(exc)})
        return _login_failed("token_invalid")
    user = repo.upsert_user(claims.issuer, claims.subject, claims.email if claims.email_verified else None, claims.name)
    token, _csrf = repo.create_session(user.id, crypto.encrypt(id_token), settings.session_ttl_hours)
    response = RedirectResponse(f"{settings.public_origin}{safe_redirect_path(attempt.redirect_after)}", status_code=302)
    response.set_cookie(
        settings.effective_cookie_name, token, max_age=settings.session_ttl_hours * 3600, httponly=True,
        secure=settings.cookie_secure, samesite="lax", path="/",
    )
    return response


def _login_failed(reason: str) -> RedirectResponse:
    return RedirectResponse(f"{get_settings().public_origin}/login-failed?reason={quote(reason)}", status_code=302)


class WorkspaceOut(BaseModel):
    tenant_id: str
    slug: str
    name: str
    role: str
    locale: str
    timezone: str


class MeOut(BaseModel):
    user_id: str
    email: str | None
    display_name: str | None
    csrf_token: str
    workspaces: list[WorkspaceOut]
    demo_mode: bool


@router.get("/me", response_model=MeOut)
def me(session: Annotated[repo.SessionInfo, Depends(current_session)]) -> MeOut:
    workspaces = repo.list_workspaces(session.user.id)
    return MeOut(
        user_id=str(session.user.id), email=session.user.email, display_name=session.user.display_name,
        csrf_token=session.csrf_secret, demo_mode=get_settings().app_env != "production",
        workspaces=[WorkspaceOut(tenant_id=str(w.tenant_id), slug=w.slug, name=w.name, role=w.role, locale=w.locale,
                                 timezone=w.timezone) for w in workspaces],
    )


class LogoutOut(BaseModel):
    logout_url: str | None


@router.post("/logout", response_model=LogoutOut)
def logout(response: Response, session: Annotated[repo.SessionInfo, Depends(current_session)]) -> LogoutOut:
    settings = get_settings()
    repo.revoke_session(session.token_hash)
    id_token = crypto.decrypt(session.id_token_enc) if session.id_token_enc else None
    try:
        url = oidc().end_session_url(id_token)
    except OidcError:
        url = None
    response.delete_cookie(settings.effective_cookie_name, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")
    return LogoutOut(logout_url=url)


class InvitationPreview(BaseModel):
    tenant_name: str
    role: str
    email: str
    usable: bool


@router.get("/invitations/preview", response_model=InvitationPreview)
def preview_invitation(token: Annotated[str, Query(min_length=16, max_length=200)]) -> InvitationPreview:
    info = repo.describe_invitation(token)
    if info is None:
        raise ApiError(404, "invitation_invalid", "Einladung nicht gefunden")
    return InvitationPreview(**info)


class AcceptIn(BaseModel):
    token: str


class AcceptOut(BaseModel):
    tenant_id: str


_INVITE_MESSAGES = {
    "invalid": "Die Einladung ist ungültig oder wurde zurückgezogen.",
    "used": "Die Einladung wurde bereits verwendet.",
    "expired": "Die Einladung ist abgelaufen.",
    "email_mismatch": "Die Einladung gehört zu einer anderen E-Mail-Adresse als Ihr Konto.",
}


@router.post("/invitations/accept", response_model=AcceptOut)
def accept_invitation(body: AcceptIn, session: Annotated[repo.SessionInfo, Depends(current_session)]) -> AcceptOut:
    try:
        tenant_id = repo.accept_invitation(body.token, session.user.id, session.user.email)
    except repo.InvitationError as exc:
        raise ApiError(400 if exc.code != "email_mismatch" else 403, f"invitation_{exc.code}", "Einladung nicht annehmbar",
                       _INVITE_MESSAGES[exc.code]) from exc
    from decision_evidence.tenancy.context import add_audit
    from decision_evidence.db.session import tenant_session
    from decision_evidence.db.engines import get_engine
    with tenant_session(get_engine("app"), tenant_id) as s:
        add_audit(s, session.user.id, "member.joined", "membership", None, {"user_id": str(session.user.id)})
    return AcceptOut(tenant_id=str(tenant_id))
