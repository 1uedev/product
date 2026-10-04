#!/bin/sh
# Backend unit + integration tests against a real PostgreSQL 18 (restricted runtime roles, RLS) in an isolated project.
# Usage: scripts/test_integration.sh [pytest args]
set -eu
. "$(dirname "$0")/lib_compose.sh"
export TEST_PROJECT="${TEST_PROJECT:-$(new_test_project)}"
export TEST_DB_PORT="${TEST_DB_PORT:-$(free_port)}"
export COMPOSE_PROJECT_NAME="$TEST_PROJECT" APP_ENV=test
trap teardown_test_project EXIT

echo "== project $TEST_PROJECT, postgres on 127.0.0.1:$TEST_DB_PORT"
compose up -d --wait postgres
compose up --no-deps --exit-code-from db-init db-init
compose run --rm --no-deps migrate

cd "$ROOT/backend"
export DB_HOST=127.0.0.1 DB_PORT="$TEST_DB_PORT" DB_NAME=decision_evidence
export DB_MIGRATOR_PASSWORD=demo-migrator-pw DB_AUTH_PASSWORD=demo-auth-pw DB_APP_PASSWORD=demo-app-pw DB_WORKER_PASSWORD=demo-worker-pw DB_OUTBOX_PASSWORD=demo-outbox-pw
export OIDC_ISSUER=http://localhost:8480/auth/realms/decision-evidence SECRET_KEY=ZGVtb2RlbW9kZW1vZGVtb2RlbW9kZW1vZGVtb2RlbW8= S3_ACCESS_KEY=demoaccesskey S3_SECRET_KEY=demosecretkey1234
uv run pytest ../tests -v "$@"
