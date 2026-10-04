from __future__ import annotations

import pytest

from decision_evidence.config import ConfigError, Settings


def prod(**kw):  # type: ignore[no-untyped-def]
    base = dict(app_env="production", public_origin="https://decisions.example.org", oidc_issuer="https://id.example.org/realms/x",
                oidc_client_secret="Zx9vQ2mLp8Rt4NwK7yUe", secret_key="Zx9vQ2mLp8Rt4NwK7yUeAb3dFg5hJk7lMn9pQr1sTu=",
                db_migrator_password="Aq8mZx2Lp0Rt4NwK7yUe", db_auth_password="Bq8mZx2Lp0Rt4NwK7yUe", db_app_password="Cq8mZx2Lp0Rt4NwK7yUe",
                db_worker_password="Dq8mZx2Lp0Rt4NwK7yUe", db_outbox_password="Eq8mZx2Lp0Rt4NwK7yUe",
                rabbitmq_url="amqp://app:Gq8mZx2Lp0Rt4NwK7yUe@rabbitmq:5672/", s3_access_key="Hq8mZx2Lp0Rt4NwK7yUe", s3_secret_key="Iq8mZx2Lp0Rt4NwK7yUe",
                ai_provider="anthropic", anthropic_api_key="sk-ant-xxxxxxxxxxxxxxxxxxxx", anthropic_model="claude-sonnet-5-5")
    base.update(kw)
    return Settings(**base)


def test_production_accepts_strong_configuration() -> None:
    prod().validate_for_runtime(needs={"api", "broker", "storage", "ai"})


def test_production_accepts_the_local_model_provider_without_cloud_credentials() -> None:
    cfg = prod(ai_provider="ollama", ollama_model="qwen3:8b", ollama_base_url="http://ollama:11434", anthropic_api_key=None, anthropic_model="")
    cfg.validate_for_runtime(needs={"api", "broker", "storage", "ai"})
    with pytest.raises(ConfigError):
        prod(ai_provider="ollama", ollama_model="").validate_for_runtime(needs={"api", "broker", "storage", "ai"})


@pytest.mark.parametrize("override", [
    {"db_app_password": "demo-password-change-me"}, {"public_origin": "http://decisions.example.org"}, {"ai_provider": "mock"},
    {"oidc_issuer": "http://localhost:8080/realms/x"}, {"secret_key": "short"}, {"oidc_client_secret": "changeme-changeme-changeme"},
])
def test_production_refuses_unsafe_configuration(override) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(ConfigError):
        prod(**override).validate_for_runtime(needs={"api", "broker", "storage", "ai"})


def test_demo_mode_allows_defaults_and_mock() -> None:
    Settings(app_env="demo", oidc_issuer="http://localhost:8480/auth/realms/x", oidc_client_secret="demo", secret_key="x").validate_for_runtime(needs={"api"})


def test_cookie_name_and_security_follow_origin() -> None:
    assert prod().effective_cookie_name.startswith("__Host-") and prod().cookie_secure
    assert Settings(public_origin="http://localhost:8480").effective_cookie_name == "de_session"
