from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from decision_evidence.db.engines import get_engine
from decision_evidence.storage.s3 import get_storage

EXPECTED_SCHEMA_REVISION = "0005"  # kept in sync with migrations/versions by tests/integration/test_schema.py

router = APIRouter(tags=["health"])


@router.get("/api/health/live")
def live() -> dict[str, str]:
    return {"status": "ok"}


def _check(name: str, fn: Any) -> dict[str, Any]:
    try:
        detail = fn()
        return {"name": name, "ok": True, **({"detail": detail} if detail else {})}
    except Exception as exc:
        return {"name": name, "ok": False, "error": type(exc).__name__}


def _db_app() -> str:
    with get_engine("app").connect() as c:
        rev = c.execute(text("SELECT version_num FROM infra.alembic_version")).scalar()
    if rev != EXPECTED_SCHEMA_REVISION:
        raise RuntimeError(f"schema revision {rev} != {EXPECTED_SCHEMA_REVISION}")
    return f"revision {rev}"


def _db_auth() -> None:
    with get_engine("auth").connect() as c:
        c.execute(text("SELECT 1 FROM identity.tenants LIMIT 1"))


def _storage() -> None:
    get_storage().ping()


@router.get("/api/health/ready")
def ready() -> JSONResponse:
    checks = [_check("database_app", _db_app), _check("database_auth", _db_auth), _check("object_storage", _storage)]
    ok = all(c["ok"] for c in checks)
    return JSONResponse({"status": "ready" if ok else "not_ready", "checks": checks}, status_code=200 if ok else 503)
