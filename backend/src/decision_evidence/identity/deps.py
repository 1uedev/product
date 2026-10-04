"""FastAPI dependencies: cookie session, CSRF and Origin checks."""

from __future__ import annotations

import hmac

from fastapi import Depends, Request

from decision_evidence.config import get_settings
from decision_evidence.errors import ApiError
from decision_evidence.identity import repository as repo

UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def current_session(request: Request) -> repo.SessionInfo:
    settings = get_settings()
    token = request.cookies.get(settings.effective_cookie_name)
    if not token:
        raise ApiError(401, "unauthenticated", "Nicht angemeldet")
    info = repo.load_session(token)
    if info is None:
        raise ApiError(401, "session_expired", "Die Sitzung ist abgelaufen")
    if request.method in UNSAFE_METHODS:
        origin = request.headers.get("origin")
        if origin is not None and origin.rstrip("/") != settings.public_origin:
            raise ApiError(403, "bad_origin", "Anfrage von unerwartetem Ursprung")
        supplied = request.headers.get("x-csrf-token", "")
        if not supplied or not hmac.compare_digest(supplied, info.csrf_secret):
            raise ApiError(403, "csrf_failed", "CSRF-Prüfung fehlgeschlagen")
    return info


SessionDep = Depends(current_session)
