"""Typed runtime configuration with startup validation.

Every process (api, worker, outbox-publisher, migrate, bootstrap) reads the same settings class but only
receives the credentials of its own database role (see compose.yaml). ``validate_for_runtime`` fails fast on
missing or unsafe configuration; in production mode known demo defaults are refused.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal
from urllib.parse import quote, urlparse

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Values that are only acceptable in demo/test mode. Production start-up refuses all of them.
DEMO_SECRET_MARKERS = ("demo", "change-me", "changeme", "example", "test-secret", "password")


class ConfigError(RuntimeError):
    """Raised when the configuration is incomplete or unsafe for the selected mode."""


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    app_env: Literal["demo", "test", "production"] = "demo"
    public_origin: str = "http://localhost:8480"
    log_level: str = "INFO"
    instance_name: str = "decision-evidence"

    # --- database (each process only gets the passwords of its own role)
    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "decision_evidence"
    db_migrator_password: SecretStr | None = None
    db_auth_password: SecretStr | None = None
    db_app_password: SecretStr | None = None
    db_worker_password: SecretStr | None = None
    db_outbox_password: SecretStr | None = None
    db_pool_size: int = 8

    # --- OIDC. oidc_issuer is the *public* issuer expected in tokens; the backchannel may use another host.
    oidc_issuer: str = ""
    oidc_internal_issuer: str = ""
    oidc_client_id: str = "decision-evidence-web"
    oidc_client_secret: SecretStr | None = None
    oidc_scopes: str = "openid profile email"
    oidc_leeway_seconds: int = 30
    session_ttl_hours: int = 8

    # 32-byte url-safe base64 key (Fernet) used to encrypt the id token and PKCE verifiers at rest
    secret_key: SecretStr | None = None

    # --- broker
    rabbitmq_url: SecretStr | None = None

    # --- object storage (S3 compatible)
    s3_endpoint_url: str = "http://storage:8333"
    s3_region: str = "us-east-1"
    s3_bucket: str = "decision-evidence"
    s3_access_key: SecretStr | None = None
    s3_secret_key: SecretStr | None = None

    # --- AI
    ai_provider: Literal["mock", "anthropic"] = "mock"
    allow_mock_ai_in_production: bool = False
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = ""
    ai_timeout_seconds: float = 60.0
    ai_max_retries: int = 2
    ai_max_output_tokens: int = 8000
    ai_max_input_chars: int = 120_000
    # optional, only used if both prices are configured; the basis is stored with every run
    ai_price_input_per_mtok: float | None = None
    ai_price_output_per_mtok: float | None = None
    ai_price_currency: str = "USD"
    ai_price_basis: str = ""
    # test-only knob to make worker-restart tests deterministic
    mock_ai_delay_seconds: float = 0.0

    # --- jobs
    job_lease_seconds: int = 60
    job_heartbeat_seconds: int = 15
    job_backoff_base_seconds: int = 5

    # --- uploads
    session_cookie_name: str = "de_session"

    @field_validator("public_origin", "oidc_issuer", "oidc_internal_issuer")
    @classmethod
    def _strip_slash(cls, v: str) -> str:
        return v.rstrip("/")

    # ------------------------------------------------------------------ derived values
    @property
    def cookie_secure(self) -> bool:
        return self.public_origin.startswith("https://")

    @property
    def effective_cookie_name(self) -> str:
        # __Host- prefix needs Secure, Path=/ and no Domain: only possible on HTTPS
        return f"__Host-{self.session_cookie_name}" if self.cookie_secure else self.session_cookie_name

    @property
    def oidc_backchannel_base(self) -> str:
        return self.oidc_internal_issuer or self.oidc_issuer

    def database_url(self, role: Literal["migrator", "auth", "app", "worker", "outbox"]) -> str:
        pw = getattr(self, f"db_{role}_password")
        if pw is None or not pw.get_secret_value():
            raise ConfigError(f"DB_{role.upper()}_PASSWORD is not set")
        user = f"de_{role}"
        return (
            f"postgresql+psycopg://{user}:{quote(pw.get_secret_value(), safe='')}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    # ------------------------------------------------------------------ validation
    def validate_for_runtime(self, *, needs: set[str]) -> None:
        """Validate configuration for a process. ``needs`` names the subsystems the process uses."""
        problems: list[str] = []
        parsed = urlparse(self.public_origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            problems.append("PUBLIC_ORIGIN must be an absolute http(s) URL")
        if "api" in needs:
            for name in ("oidc_issuer", "oidc_client_id"):
                if not getattr(self, name):
                    problems.append(f"{name.upper()} is required")
            for name in ("oidc_client_secret", "secret_key"):
                if getattr(self, name) is None:
                    problems.append(f"{name.upper()} is required")
        if "broker" in needs and self.rabbitmq_url is None:
            problems.append("RABBITMQ_URL is required")
        if "storage" in needs:
            for name in ("s3_access_key", "s3_secret_key"):
                if getattr(self, name) is None:
                    problems.append(f"{name.upper()} is required")
        if self.ai_provider == "anthropic" and ("ai" in needs or "api" in needs):
            if self.anthropic_api_key is None:
                problems.append("ANTHROPIC_API_KEY is required when AI_PROVIDER=anthropic")
            if not self.anthropic_model:
                problems.append("ANTHROPIC_MODEL is required when AI_PROVIDER=anthropic")
        if self.app_env == "production":
            problems.extend(self._production_problems())
        if problems:
            raise ConfigError("invalid configuration: " + "; ".join(problems))

    def _production_problems(self) -> list[str]:
        problems: list[str] = []
        if not self.public_origin.startswith("https://"):
            problems.append("production requires an https PUBLIC_ORIGIN")
        if self.ai_provider == "mock" and not self.allow_mock_ai_in_production:
            problems.append("AI_PROVIDER=mock (demo adapter) is not allowed in production")
        secrets: dict[str, SecretStr | None] = {
            "DB_MIGRATOR_PASSWORD": self.db_migrator_password,
            "DB_AUTH_PASSWORD": self.db_auth_password,
            "DB_APP_PASSWORD": self.db_app_password,
            "DB_WORKER_PASSWORD": self.db_worker_password,
            "DB_OUTBOX_PASSWORD": self.db_outbox_password,
            "OIDC_CLIENT_SECRET": self.oidc_client_secret,
            "SECRET_KEY": self.secret_key,
            "RABBITMQ_URL": self.rabbitmq_url,
            "S3_ACCESS_KEY": self.s3_access_key,
            "S3_SECRET_KEY": self.s3_secret_key,
        }
        for name, value in secrets.items():
            if value is None:
                continue  # missing values are reported by the subsystem check of the process that needs them
            raw = value.get_secret_value().lower()
            if any(marker in raw for marker in DEMO_SECRET_MARKERS):
                problems.append(f"{name} looks like a demo/default value")
            if len(raw) < 16 and name != "RABBITMQ_URL":
                problems.append(f"{name} is shorter than 16 characters")
        if "localhost" in self.oidc_issuer or "127.0.0.1" in self.oidc_issuer:
            problems.append("OIDC_ISSUER must not point at localhost in production")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()


__all__ = ["ConfigError", "Field", "Settings", "get_settings", "reset_settings_cache"]
