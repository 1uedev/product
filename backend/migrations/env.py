from __future__ import annotations

from alembic import context
from sqlalchemy import create_engine, pool

from decision_evidence.config import get_settings
from decision_evidence.db import models  # noqa: F401  (register metadata)
from decision_evidence.db.base import Base

target_metadata = Base.metadata
# Alembic keeps its version table in the identity-independent public-free place: schema "infra" is owned by the migrator.
VERSION_SCHEMA = "infra"


def run_migrations_online() -> None:
    settings = get_settings()
    engine = create_engine(settings.database_url("migrator"), poolclass=pool.NullPool)
    with engine.connect() as connection:
        # create the infra schema before alembic touches its version table
        connection.exec_driver_sql("CREATE SCHEMA IF NOT EXISTS infra")
        connection.commit()
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            version_table="alembic_version",
            version_table_schema=VERSION_SCHEMA,
            transaction_per_migration=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("offline mode is not supported; migrations need a live database")
run_migrations_online()
