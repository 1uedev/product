#!/bin/sh
# Verifies that compose.prod.yaml accepts a complete production environment and rejects an incomplete one. No containers are started.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENVF="$(mktemp)"; trap 'rm -f "$ENVF"' EXIT
cat > "$ENVF" <<'ENV'
COMPOSE_PROJECT_NAME=de-test-prodcheck
DE_VERSION=check
PUBLIC_HOST=decisions.example.test
PUBLIC_ORIGIN=https://decisions.example.test
AI_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-check
ANTHROPIC_MODEL=claude-sonnet-5-5
POSTGRES_PASSWORD=Kq7vN2xLm9Rt4WpZ8yUeHb3c
DB_MIGRATOR_PASSWORD=Aq7vN2xLm9Rt4WpZ8yUeHb3d
DB_AUTH_PASSWORD=Bq7vN2xLm9Rt4WpZ8yUeHb3e
DB_APP_PASSWORD=Cq7vN2xLm9Rt4WpZ8yUeHb3f
DB_WORKER_PASSWORD=Dq7vN2xLm9Rt4WpZ8yUeHb3g
DB_OUTBOX_PASSWORD=Eq7vN2xLm9Rt4WpZ8yUeHb3h
KEYCLOAK_DB_PASSWORD=Fq7vN2xLm9Rt4WpZ8yUeHb3i
KEYCLOAK_ADMIN_PASSWORD=Gq7vN2xLm9Rt4WpZ8yUeHb3j
OIDC_CLIENT_SECRET=Hq7vN2xLm9Rt4WpZ8yUeHb3k
RABBITMQ_PASSWORD=Iq7vN2xLm9Rt4WpZ8yUeHb3l
S3_ACCESS_KEY=Jq7vN2xLm9Rt4WpZ8yUeHb3m
S3_SECRET_KEY=Kq7vN2xLm9Rt4WpZ8yUeHb3n
SECRET_KEY=Zx9vQ2mLp8Rt4NwK7yUeAb3dFg5hJk7lMn9pQr1sTu8=
ENV
docker compose -f "$ROOT/compose.prod.yaml" --env-file "$ENVF" config -q
for var in DB_APP_PASSWORD SECRET_KEY OIDC_CLIENT_SECRET PUBLIC_ORIGIN DE_VERSION; do
  grep -v "^$var=" "$ENVF" > "$ENVF.missing"
  if docker compose -f "$ROOT/compose.prod.yaml" --env-file "$ENVF.missing" config -q 2>/dev/null; then echo "FAIL: missing $var was accepted"; rm -f "$ENVF.missing"; exit 1; fi
done
rm -f "$ENVF.missing"
echo "production compose configuration: complete environment accepted, missing secrets rejected"
