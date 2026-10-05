"""Unauthenticated configuration for the login page. Demo hints are only exposed outside production."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from decision_evidence.config import get_settings

router = APIRouter(prefix="/api/v1/public", tags=["public"])

DEMO_PASSWORD_HINT = "demo-Passw0rd!"
DEMO_ACCOUNTS = [
    {"email": "alice@lumen.example", "name": "Alice Owner", "role": "Owner (Lumen Analytics), Workspaces: Lumen, E2E"},
    {"email": "dora@lumen.example", "name": "Dora Admin", "role": "Admin (Lumen Analytics)"},
    {"email": "bob@lumen.example", "name": "Bob Editor", "role": "Editor (Lumen Analytics)"},
    {"email": "vera@lumen.example", "name": "Vera Viewer", "role": "Viewer (Lumen Analytics)"},
    {"email": "finn@fjord.example", "name": "Finn Fjord", "role": "Owner (Fjord Systems, getrennter Mandant)"},
]


@router.get("/config")
def public_config() -> dict[str, Any]:
    s = get_settings()
    demo = s.app_env != "production"
    return {
        "demo_mode": demo,
        "ai": {"provider": s.ai_provider, "demo": s.ai_provider == "mock"},
        "demo_accounts": DEMO_ACCOUNTS if demo else [],
        "demo_password": DEMO_PASSWORD_HINT if demo else None,
        "locale_default": "de-DE",
    }
