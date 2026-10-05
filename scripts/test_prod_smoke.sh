#!/bin/sh
# Production-mode smoke test in an isolated project (de-test-*): compose.prod.yaml with generated secrets, HTTPS through Caddy's
# internal CA, no demo data. Creates the first Keycloak user with kcadm and the first workspace with the admin CLI exactly as
# described in docs/operations/production.md, then runs the browser test 99-prod-smoke (invitation, login, secure cookie).
# A fake public host name is mapped to 127.0.0.1 inside the browser; no DNS and no public certificate is involved.
# No call to the AI provider is made (a dummy key satisfies the production configuration check).
set -eu
. "$(dirname "$0")/lib_compose.sh"
TEST_PROJECT="$(new_test_project)"
HTTPS_PORT="${HTTPS_PORT:-$(free_port)}"; HTTP_PORT="${HTTP_PORT:-$(free_port)}"
HOST="decisions.example.test"
ENVF="$(mktemp)"
rand() { openssl rand -base64 24 | tr '+/' '-_' | tr -d '='; }
cat > "$ENVF" <<ENV
COMPOSE_PROJECT_NAME=$TEST_PROJECT
DE_VERSION=smoke
PUBLIC_HOST=$HOST
PUBLIC_ORIGIN=https://$HOST:$HTTPS_PORT
HTTP_PORT=$HTTP_PORT
HTTPS_PORT=$HTTPS_PORT
CADDY_TLS_MODE=internal
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-not-used-in-this-test
ANTHROPIC_MODEL=claude-sonnet-5-5
POSTGRES_PASSWORD=$(rand)
DB_MIGRATOR_PASSWORD=$(rand)
DB_AUTH_PASSWORD=$(rand)
DB_APP_PASSWORD=$(rand)
DB_WORKER_PASSWORD=$(rand)
DB_OUTBOX_PASSWORD=$(rand)
KEYCLOAK_DB_PASSWORD=$(rand)
KEYCLOAK_ADMIN_PASSWORD=$(rand)
OIDC_CLIENT_SECRET=$(rand)
RABBITMQ_PASSWORD=$(rand)
S3_ACCESS_KEY=$(rand | tr -d '_-')
S3_SECRET_KEY=$(rand)
SECRET_KEY=$(python3 -c "import base64,os; print(base64.urlsafe_b64encode(os.urandom(32)).decode())")
ENV
chmod 600 "$ENVF"
P() { docker compose -p "$TEST_PROJECT" -f "$ROOT/compose.prod.yaml" --env-file "$ENVF" "$@"; }
cleanup() {
  case "$TEST_PROJECT" in de-test-*) [ "${KEEP_TEST_PROJECT:-0}" = "1" ] || P down -v --remove-orphans >/dev/null 2>&1 || true ;; esac
  rm -f "$ENVF"
}
trap cleanup EXIT

echo "== project $TEST_PROJECT, https://$HOST:$HTTPS_PORT (internal CA)"
P up -d --build --wait --wait-timeout 420
OWNER="owner@example.test"; PASSWORD="$(rand)"
KC="P exec -T keycloak /opt/keycloak/bin/kcadm.sh"
KCADMIN_PW="$(grep '^KEYCLOAK_ADMIN_PASSWORD=' "$ENVF" | cut -d= -f2-)"
$KC config credentials --server http://127.0.0.1:8080/auth --realm master --user admin --password "$KCADMIN_PW"
$KC create users -r decision-evidence -s "username=$OWNER" -s "email=$OWNER" -s firstName=Olivia -s lastName=Owner -s emailVerified=true -s enabled=true
$KC set-password -r decision-evidence --username "$OWNER" --new-password "$PASSWORD"
LINK="$(P run --rm --no-deps -T bootstrap python -m decision_evidence.tools.admin create-tenant smoke "Smoke GmbH" "$OWNER" | grep -o 'invite?token=[A-Za-z0-9_-]*')"
TOKEN="${LINK#invite?token=}"
[ -n "$TOKEN" ] || { echo "no invitation token in the admin output" >&2; exit 1; }

echo "== readiness through the gateway"
curl -fsS --noproxy '*' --resolve "$HOST:$HTTPS_PORT:127.0.0.1" -k "https://$HOST:$HTTPS_PORT/api/health/ready"; echo
echo "== demo adapter and docs are refused/disabled in production"
code="$(curl -s --noproxy '*' --resolve "$HOST:$HTTPS_PORT:127.0.0.1" -k -o /dev/null -w '%{http_code}' "https://$HOST:$HTTPS_PORT/api/docs")"
echo "GET /api/docs -> $code"; [ "$code" != "200" ]

cd "$ROOT/tests/e2e"
[ -d node_modules ] || pnpm install --frozen-lockfile
E2E_PROD_ORIGIN="https://$HOST:$HTTPS_PORT" E2E_PROD_MAP_HOST=1 E2E_PROD_USER="$OWNER" E2E_PROD_PASSWORD="$PASSWORD" E2E_PROD_INVITE_TOKEN="$TOKEN" \
  pnpm exec playwright test -c playwright.prod.config.ts
echo "PRODUCTION SMOKE TEST PASSED"
