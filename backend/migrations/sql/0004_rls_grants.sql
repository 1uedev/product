-- 0004: row level security and grants. See docs/architecture/grant-matrix.md.
-- Every app.* table is tenant-isolated with ENABLE + FORCE ROW LEVEL SECURITY. Policies carry USING and WITH CHECK.
-- A missing tenant context (app.tenant_id unset) yields NULL, so no row matches and nothing can be written.

DO $$
DECLARE t text;
BEGIN
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'app'
  LOOP
    EXECUTE format('ALTER TABLE app.%I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE app.%I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format(
      'CREATE POLICY tenant_isolation ON app.%I FOR ALL TO de_app, de_worker, de_migrator
         USING (tenant_id = infra.current_tenant_id())
         WITH CHECK (tenant_id = infra.current_tenant_id())', t);
  END LOOP;
END
$$;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO de_app, de_worker;
-- append-only audit trail: no UPDATE/DELETE for runtime roles (a trigger blocks it for everybody else)
REVOKE UPDATE, DELETE ON app.audit_events FROM de_app, de_worker;
-- the problem history is append-only as well
REVOKE UPDATE ON app.problem_history FROM de_app, de_worker;
-- tenants are created by the bootstrap role only
GRANT USAGE ON ALL SEQUENCES IN SCHEMA app TO de_app, de_worker;

-- ---------------------------------------------------------------------------
-- infra.outbox: RLS with role specific policies.
--   de_app / de_worker may only INSERT rows for their own tenant context (job + outbox in one transaction).
--   de_outbox may read and mark rows of all tenants but sees mediation metadata only (column grants).
-- ---------------------------------------------------------------------------
ALTER TABLE infra.outbox ENABLE ROW LEVEL SECURITY;
ALTER TABLE infra.outbox FORCE ROW LEVEL SECURITY;

CREATE POLICY outbox_enqueue ON infra.outbox FOR INSERT TO de_app, de_worker, de_migrator
    WITH CHECK (tenant_id = infra.current_tenant_id());
CREATE POLICY outbox_tenant_read ON infra.outbox FOR SELECT TO de_app, de_worker, de_migrator
    USING (tenant_id = infra.current_tenant_id());
CREATE POLICY outbox_publisher_read ON infra.outbox FOR SELECT TO de_outbox USING (true);
CREATE POLICY outbox_publisher_mark ON infra.outbox FOR UPDATE TO de_outbox USING (true) WITH CHECK (true);
CREATE POLICY outbox_recovery_read ON infra.outbox FOR SELECT TO de_recovery USING (true);

GRANT INSERT, SELECT ON infra.outbox TO de_app, de_worker;
GRANT SELECT (id, tenant_id, job_id, event_type, schema_version, correlation_id, available_at,
              published_at, attempts, last_error_code, created_at) ON infra.outbox TO de_outbox;
GRANT UPDATE (published_at, attempts, last_error_code, available_at) ON infra.outbox TO de_outbox;

-- ---------------------------------------------------------------------------
-- Cross-tenant recovery without giving the worker blanket read access:
-- SECURITY DEFINER functions owned by the NOLOGIN role de_recovery, which only has column grants
-- on the few columns needed to find stale work. They return identifiers only.
-- ---------------------------------------------------------------------------
GRANT SELECT (id, tenant_id, kind, status, lease_expires_at, updated_at, attempts) ON app.jobs TO de_recovery;
GRANT SELECT (id, tenant_id, status, object_key, created_at, updated_at) ON app.files TO de_recovery;
GRANT SELECT (tenant_id, job_id, published_at) ON infra.outbox TO de_recovery;
CREATE POLICY recovery_read_jobs ON app.jobs FOR SELECT TO de_recovery USING (true);
CREATE POLICY recovery_read_files ON app.files FOR SELECT TO de_recovery USING (true);

CREATE FUNCTION infra.find_stale_jobs(p_limit integer DEFAULT 50)
RETURNS TABLE (tenant_id uuid, job_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS
$$
SELECT j.tenant_id, j.id
  FROM app.jobs j
 WHERE (j.status = 'running' AND j.lease_expires_at < now())
    OR (j.status = 'queued' AND j.updated_at < now() - interval '10 minutes'
        AND NOT EXISTS (SELECT 1 FROM infra.outbox o
                         WHERE o.tenant_id = j.tenant_id AND o.job_id = j.id AND o.published_at IS NULL))
 ORDER BY j.updated_at
 LIMIT least(greatest(p_limit, 1), 500)
$$;

CREATE FUNCTION infra.find_stale_files(p_limit integer DEFAULT 50)
RETURNS TABLE (tenant_id uuid, file_id uuid)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS
$$
SELECT f.tenant_id, f.id
  FROM app.files f
 WHERE (f.status = 'pending' AND f.created_at < now() - interval '30 minutes')
    OR (f.status = 'deleting' AND f.updated_at < now() - interval '10 minutes')
 ORDER BY f.created_at
 LIMIT least(greatest(p_limit, 1), 500)
$$;

-- ALTER ... OWNER requires CREATE on the schema for the new owner; it is revoked again right away
GRANT CREATE ON SCHEMA infra TO de_recovery;
ALTER FUNCTION infra.find_stale_jobs(integer) OWNER TO de_recovery;
ALTER FUNCTION infra.find_stale_files(integer) OWNER TO de_recovery;
REVOKE CREATE ON SCHEMA infra FROM de_recovery;
REVOKE ALL ON FUNCTION infra.find_stale_jobs(integer), infra.find_stale_files(integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION infra.find_stale_jobs(integer), infra.find_stale_files(integer) TO de_worker;
GRANT USAGE ON SCHEMA infra TO de_recovery;

-- readiness probe: runtime roles may read the migration revision, nothing else in the version table
GRANT SELECT ON infra.alembic_version TO de_app, de_worker;
