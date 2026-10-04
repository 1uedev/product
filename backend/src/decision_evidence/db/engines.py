"""One engine per database role. Runtime engines are never superuser, never table owner, never BYPASSRLS."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from sqlalchemy import Engine, create_engine

from decision_evidence.config import get_settings

Role = Literal["migrator", "auth", "app", "worker", "outbox"]


@lru_cache
def get_engine(role: Role) -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url(role),
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_pool_size,
        pool_pre_ping=True,
        pool_recycle=1800,
        pool_reset_on_return="rollback",
        connect_args={"application_name": f"decision-evidence-{role}", "connect_timeout": 10},
    )


def dispose_engines() -> None:
    for role in ("migrator", "auth", "app", "worker", "outbox"):
        if get_engine.cache_info().currsize:
            get_engine(role).dispose()  # type: ignore[arg-type]
    get_engine.cache_clear()
