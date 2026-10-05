from __future__ import annotations

import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from decision_evidence.db.context import current_request_id
from decision_evidence.observability.logging import get_logger

log = get_logger("decision_evidence.access")
_VALID_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")

SECURITY_HEADERS = {
    b"x-content-type-options": b"nosniff",
    b"referrer-policy": b"same-origin",
    b"cache-control": b"no-store",
}


class RequestContextMiddleware:
    """Assigns/propagates X-Request-ID and writes one access log line without query strings or bodies."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope["headers"])
        incoming = headers.get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _VALID_ID.match(incoming) else uuid.uuid4().hex
        token = current_request_id.set(request_id)
        started = time.perf_counter()
        status = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                hdrs = list(message.get("headers", []))
                hdrs.append((b"x-request-id", request_id.encode()))
                existing = {k.lower() for k, _ in hdrs}
                hdrs.extend((k, v) for k, v in SECURITY_HEADERS.items() if k not in existing)
                message["headers"] = hdrs
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            path = scope.get("path", "")
            if not path.startswith("/api/health"):
                log.info("request", extra={"method": scope.get("method"), "path": path, "status": status,
                                           "duration_ms": round((time.perf_counter() - started) * 1000, 1)})
            current_request_id.reset(token)
