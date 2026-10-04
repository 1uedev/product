#!/bin/sh
# Developer helper for iterating on browser tests: empties the E2E workspace of a RUNNING test stack
# (project name must start with de-test-). Empties the two E2E workspaces only; never touches other workspaces.
set -eu
PROJECT="${1:?usage: reset_e2e_workspace.sh <de-test-project>}"
case "$PROJECT" in de-test-*) ;; *) echo "refusing: $PROJECT is not an isolated test project" >&2; exit 1 ;; esac
docker compose -p "$PROJECT" exec -T postgres psql -U postgres -d decision_evidence -v ON_ERROR_STOP=1 -q <<'SQL'
DO $$
DECLARE tid uuid; t text;
BEGIN
  SET LOCAL session_replication_role = replica;   -- skip FK/append-only triggers for this maintenance task only
  FOR tid IN SELECT id FROM identity.tenants WHERE slug IN ('e2e-clean', 'e2e-resilience') LOOP
    FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'app' AND tablename <> 'tenant_settings' LOOP
      EXECUTE format('DELETE FROM app.%I WHERE tenant_id = %L', t, tid);
    END LOOP;
    DELETE FROM infra.outbox WHERE tenant_id = tid;
  END LOOP;
END $$;
SQL
echo "e2e workspaces of $PROJECT emptied"
