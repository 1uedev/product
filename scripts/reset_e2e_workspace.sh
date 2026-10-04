#!/bin/sh
# Developer helper for iterating on browser tests: empties the E2E workspace of a RUNNING test stack
# (project name must start with de-test-). Never touches other workspaces.
set -eu
PROJECT="${1:?usage: reset_e2e_workspace.sh <de-test-project>}"
case "$PROJECT" in de-test-*) ;; *) echo "refusing: $PROJECT is not an isolated test project" >&2; exit 1 ;; esac
docker compose -p "$PROJECT" exec -T postgres psql -U postgres -d decision_evidence -v ON_ERROR_STOP=1 -q <<'SQL'
DO $$
DECLARE tid uuid; t text;
BEGIN
  SELECT id INTO tid FROM identity.tenants WHERE slug = 'e2e-clean';
  SET LOCAL session_replication_role = replica;   -- skip FK/append-only triggers for this maintenance task only
  FOR t IN SELECT tablename FROM pg_tables WHERE schemaname = 'app' AND tablename <> 'tenant_settings' LOOP
    EXECUTE format('DELETE FROM app.%I WHERE tenant_id = %L', t, tid);
  END LOOP;
  DELETE FROM infra.outbox WHERE tenant_id = tid;
  DELETE FROM app.scoring_policies WHERE tenant_id = tid;
END $$;
SQL
echo "e2e workspace of $PROJECT emptied"
