#!/bin/sh
# Browser end-to-end tests against a complete, freshly built stack in an isolated compose project (own volumes, own port).
# Real Keycloak login, real API, worker, broker, storage. Usage: scripts/test_e2e.sh [playwright args]
# Environment: KEEP_TEST_PROJECT=1 keeps the stack afterwards, TEST_PROJECT=de-test-xyz reuses a running stack,
#              OLLAMA_OVERLAY=1 AI_PROVIDER=ollama OLLAMA_MODEL=<model> starts a real Ollama container too (then run: scripts/test_e2e.sh 40-ollama),
#              CHROMIUM_PATH=/path/to/chrome uses an existing browser, EXTRA_CA_BUNDLE=/path/ca.pem for TLS-inspecting build networks.
set -eu
. "$(dirname "$0")/lib_compose.sh"
REUSE=0
[ -n "${TEST_PROJECT:-}" ] && REUSE=1
export TEST_PROJECT="${TEST_PROJECT:-$(new_test_project)}"
export APP_PORT="${APP_PORT:-$(free_port)}"
export COMPOSE_PROJECT_NAME="$TEST_PROJECT" APP_ENV=test
export TEST_DB_PORT="${TEST_DB_PORT:-$(free_port)}"
trap teardown_test_project EXIT

echo "== project $TEST_PROJECT, gateway http://localhost:$APP_PORT"
if [ "$REUSE" = "0" ]; then
  compose up -d --build --wait --wait-timeout 420
else
  echo "reusing running project"
fi

cd "$ROOT/tests/e2e"
[ -d node_modules ] || pnpm install --frozen-lockfile
if [ -z "${CHROMIUM_PATH:-}" ] && ! ls "${PLAYWRIGHT_BROWSERS_PATH:-$HOME/.cache/ms-playwright}"/chromium-* >/dev/null 2>&1; then
  pnpm exec playwright install chromium
fi
export E2E_BASE_URL="http://localhost:$APP_PORT" E2E_COMPOSE_PROJECT="$TEST_PROJECT"
export E2E_COMPOSE_FILES="$ROOT/compose.yaml:$ROOT/compose.test.yaml"
[ "${OLLAMA_OVERLAY:-0}" = "1" ] && E2E_COMPOSE_FILES="$E2E_COMPOSE_FILES:$ROOT/compose.ollama.yaml"
[ "${OLLAMA_OVERLAY:-0}" = "1" ] && export E2E_AI_PROVIDER=ollama
pnpm exec playwright test "$@"
