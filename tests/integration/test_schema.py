"""The schema delivered by the migrations must satisfy the tenancy rules of the specification."""

from __future__ import annotations

import pytest
from sqlalchemy import text

from decision_evidence.db import models  # noqa: F401
from decision_evidence.db.base import Base
from decision_evidence.db.engines import get_engine

EXPECTED_APP_TABLES = {
    "files", "source_records", "source_chunks", "jobs", "ai_runs", "audit_events",
    "customer_accounts", "opportunities", "feedback_items", "problems", "problem_evidence", "initiatives",
    "initiative_assumptions", "scoring_policies", "decision_documents", "decision_comments", "decision_evidence",
    # additional tables documented in docs/architecture/data-model.md
    "tenant_settings", "import_batches", "problem_history",
}


@pytest.fixture(scope="module")
def conn():  # type: ignore[no-untyped-def]
    with get_engine("migrator").connect() as c:
        yield c


def test_all_expected_tables_exist(conn) -> None:  # type: ignore[no-untyped-def]
    app = {r[0] for r in conn.execute(text("select tablename from pg_tables where schemaname='app'"))}
    assert app == EXPECTED_APP_TABLES
    identity = {r[0] for r in conn.execute(text("select tablename from pg_tables where schemaname='identity'"))}
    assert {"users", "tenants", "memberships", "sessions", "invitations"} <= identity
    infra = {r[0] for r in conn.execute(text("select tablename from pg_tables where schemaname='infra'"))}
    assert "outbox" in infra


def test_every_app_table_has_not_null_tenant_id_and_composite_unique(conn) -> None:  # type: ignore[no-untyped-def]
    for table in EXPECTED_APP_TABLES:
        nullable = conn.execute(text(
            "select is_nullable from information_schema.columns where table_schema='app' and table_name=:t and column_name='tenant_id'"),
            {"t": table}).scalar()
        assert nullable == "NO", f"{table}.tenant_id must be NOT NULL"
        has_unique = conn.execute(text("""
            select count(*) from pg_constraint c join pg_class r on r.oid=c.conrelid
             join pg_namespace n on n.oid=r.relnamespace
             where n.nspname='app' and r.relname=:t and c.contype in ('u','p')
               and (select array_agg(a.attname::text order by a.attname::text) from unnest(c.conkey) k
                    join pg_attribute a on a.attrelid=c.conrelid and a.attnum=k) = array['id','tenant_id']"""),
            {"t": table}).scalar()
        assert has_unique == 1, f"{table} needs UNIQUE(tenant_id, id)"


def test_rls_enabled_forced_with_using_and_check(conn) -> None:  # type: ignore[no-untyped-def]
    rows = conn.execute(text("""
        select c.relname, c.relrowsecurity, c.relforcerowsecurity
          from pg_class c join pg_namespace n on n.oid=c.relnamespace
         where n.nspname in ('app') and c.relkind='r'""")).all()
    assert {r[0] for r in rows} == EXPECTED_APP_TABLES
    for name, enabled, forced in rows:
        assert enabled and forced, f"RLS not enabled+forced on app.{name}"
        pols = conn.execute(text("""
            select p.polname, p.polqual is not null, p.polwithcheck is not null from pg_policy p
             join pg_class c on c.oid=p.polrelid join pg_namespace n on n.oid=c.relnamespace
             where n.nspname='app' and c.relname=:t and p.polname='tenant_isolation'"""), {"t": name}).all()
        assert pols and pols[0][1] and pols[0][2], f"app.{name} needs a tenant_isolation policy with USING and WITH CHECK"
    outbox = conn.execute(text("select relrowsecurity, relforcerowsecurity from pg_class where oid='infra.outbox'::regclass")).one()
    assert tuple(outbox) == (True, True)


def test_runtime_roles_are_unprivileged(conn) -> None:  # type: ignore[no-untyped-def]
    rows = conn.execute(text("""
        select rolname, rolsuper, rolbypassrls, rolcreatedb, rolcreaterole, rolcanlogin from pg_roles
         where rolname in ('de_auth','de_app','de_worker','de_outbox','de_recovery')""")).all()
    assert len(rows) == 5
    for name, sup, bypass, createdb, createrole, _login in rows:
        assert not (sup or bypass or createdb or createrole), name
    owners = {r[0] for r in conn.execute(text(
        "select distinct tableowner from pg_tables where schemaname in ('app','identity','infra')"))}
    assert owners == {"de_migrator"}


def test_orm_models_match_database_columns(conn) -> None:  # type: ignore[no-untyped-def]
    for table in Base.metadata.tables.values():
        db_cols = {r[0] for r in conn.execute(text(
            "select column_name from information_schema.columns where table_schema=:s and table_name=:t"),
            {"s": table.schema, "t": table.name})}
        orm_cols = {c.name for c in table.columns}
        assert orm_cols == db_cols, f"{table.schema}.{table.name}: orm-only {orm_cols - db_cols}, db-only {db_cols - orm_cols}"


def test_tenant_composite_foreign_keys_everywhere(conn) -> None:  # type: ignore[no-untyped-def]
    """Every FK between two app tables must include tenant_id, so cross-tenant references fail in the database."""
    rows = conn.execute(text("""
        select r.relname, c.conname, array_length(c.conkey,1) as ncols,
               (select bool_or(a.attname='tenant_id') from unnest(c.conkey) k join pg_attribute a on a.attrelid=c.conrelid and a.attnum=k) as has_tenant
          from pg_constraint c join pg_class r on r.oid=c.conrelid join pg_namespace n on n.oid=r.relnamespace
          join pg_class f on f.oid=c.confrelid join pg_namespace fn on fn.oid=f.relnamespace
         where c.contype='f' and n.nspname in ('app','infra') and fn.nspname='app'""")).all()
    assert rows
    for table, name, ncols, has_tenant in rows:
        assert ncols == 2 and has_tenant, f"{table}.{name} must be a composite (tenant_id, id) foreign key"


def test_expected_schema_revision_matches_the_alembic_head() -> None:
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from decision_evidence.observability.health import EXPECTED_SCHEMA_REVISION

    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "backend" / "migrations"))
    assert ScriptDirectory.from_config(cfg).get_current_head() == EXPECTED_SCHEMA_REVISION
