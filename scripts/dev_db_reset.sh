#!/bin/sh
# Developer helper: recreate the application database inside a throw-away development PostgreSQL
# (the container started by scripts/test_integration.sh or "docker run ... -p 127.0.0.1:55432:5432").
# It refuses to touch anything that is not listening on 127.0.0.1:55432 and named decision_evidence.
set -eu
: "${PGHOST:=127.0.0.1}" "${PGPORT:=55432}"
[ "$PGHOST" = "127.0.0.1" ] && [ "$PGPORT" = "55432" ] || { echo "refusing: only the local dev database may be reset" >&2; exit 1; }
psql -X -q -d postgres -c "DROP DATABASE IF EXISTS decision_evidence WITH (FORCE)"
sh "$(dirname "$0")/../infra/postgres/db-init.sh"
(cd "$(dirname "$0")/../backend" && uv run alembic upgrade head)
