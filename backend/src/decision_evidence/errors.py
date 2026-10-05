"""RFC 9457 problem details with request correlation."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from decision_evidence.db.context import current_request_id
from decision_evidence.observability.logging import get_logger

log = get_logger(__name__)


class ApiError(Exception):
    def __init__(self, status: int, code: str, title: str, detail: str | None = None, **extra: Any) -> None:
        super().__init__(title)
        self.status = status
        self.code = code
        self.title = title
        self.detail = detail
        self.extra = extra


def not_found(what: str = "Objekt") -> ApiError:
    return ApiError(404, "not_found", f"{what} nicht gefunden")


def forbidden(detail: str = "Dafür fehlt die Berechtigung.") -> ApiError:
    return ApiError(403, "forbidden", "Keine Berechtigung", detail)


def conflict(code: str, title: str, detail: str | None = None, **extra: Any) -> ApiError:
    return ApiError(409, code, title, detail, **extra)


def bad_request(code: str, title: str, detail: str | None = None, **extra: Any) -> ApiError:
    return ApiError(400, code, title, detail, **extra)


def problem(status: int, code: str, title: str, detail: str | None = None, **extra: Any) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://decision-evidence.invalid/problems/{code}",
        "title": title,
        "status": status,
        "code": code,
        "request_id": current_request_id.get(),
    }
    if detail:
        body["detail"] = detail
    body.update(extra)
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return problem(exc.status, exc.code, exc.title, exc.detail, **exc.extra)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"field": ".".join(str(p) for p in e.get("loc", ())[1:]), "message": e.get("msg", "")}
            for e in exc.errors()
        ]
        return problem(422, "validation_error", "Eingabe ungültig", errors=errors)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        titles = {401: "Nicht angemeldet", 403: "Keine Berechtigung", 404: "Nicht gefunden", 405: "Methode nicht erlaubt"}
        return problem(exc.status_code, f"http_{exc.status_code}", titles.get(exc.status_code, "Fehler"),
                       exc.detail if isinstance(exc.detail, str) else None)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # never leak internals or payloads; the request id lets operators find the stack trace in the logs
        log.exception("unhandled error", extra={"error_type": type(exc).__name__})
        return problem(500, "internal_error", "Interner Fehler", "Bitte Support mit der request_id kontaktieren.")
