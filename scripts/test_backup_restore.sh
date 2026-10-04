#!/bin/sh
# Proves that backup + restore work: seeded stack A -> backup -> A is stopped (disaster) -> restore into a NEW isolated project B ->
# row counts, object consistency and a real browser login against B. Only touches projects named de-test-*.
set -eu
. "$(dirname "$0")/lib_compose.sh"
A="$(new_test_project)"; B="$(new_test_project)"
export APP_PORT="${APP_PORT:-$(free_port)}" APP_ENV=test
BACKUP="$(mktemp -d /tmp/de-backup-XXXXXX)/backup"
COMPOSE_FILES="-f $ROOT/compose.yaml -f $ROOT/compose.test.yaml"
cleanup() {
  for p in "$A" "$B"; do
    case "$p" in de-test-*) docker compose -p "$p" $COMPOSE_FILES down -v --remove-orphans >/dev/null 2>&1 || true ;; esac
  done
  rm -rf "$(dirname "$BACKUP")"
}
[ "${KEEP_TEST_PROJECT:-0}" = "1" ] || trap cleanup EXIT
counts() {  # project -> table counts as one line
  docker compose -p "$1" $COMPOSE_FILES exec -T postgres psql -U postgres -d decision_evidence -Atc \
   "select (select count(*) from identity.users)||','||(select count(*) from identity.memberships)||','||(select count(*) from app.customer_accounts)||','||(select count(*) from app.opportunities)||','||(select count(*) from app.feedback_items)||','||(select count(*) from app.source_chunks)||','||(select count(*) from app.problems)||','||(select count(*) from app.problem_evidence)||','||(select count(*) from app.decision_documents)||','||(select count(*) from app.files)||','||(select count(*) from app.audit_events)"
}

echo "== A: build and start $A on port $APP_PORT"
docker compose -p "$A" $COMPOSE_FILES up -d --build --wait --wait-timeout 420
BEFORE="$(counts "$A")"; echo "counts A: $BEFORE"

echo "== backup"
sh "$ROOT/scripts/backup.sh" --project "$A" --compose-file "$ROOT/compose.yaml:$ROOT/compose.test.yaml" --out "$BACKUP"
test -s "$BACKUP/decision_evidence.dump" && test -s "$BACKUP/objects-manifest.json"
echo "objects in backup: $(find "$BACKUP/objects" -type f | wc -l)"

echo "== restore refuses unsafe targets"
if sh "$ROOT/scripts/restore.sh" --from "$BACKUP" --project "$A" --compose-file "$ROOT/compose.yaml:$ROOT/compose.test.yaml" 2>/dev/null; then echo "FAIL: restore into the source project must be refused"; exit 1; fi
echo "ok: refused source project"

echo "== disaster: A is stopped (volumes kept, restore never deletes anything); restore into new project $B"
docker compose -p "$A" $COMPOSE_FILES stop >/dev/null
sh "$ROOT/scripts/restore.sh" --from "$BACKUP" --project "$B" --compose-file "$ROOT/compose.yaml:$ROOT/compose.test.yaml"
AFTER="$(counts "$B")"; echo "counts B: $AFTER"
[ "$BEFORE" = "$AFTER" ] || { echo "FAIL: row counts differ"; exit 1; }
if sh "$ROOT/scripts/restore.sh" --from "$BACKUP" --project "$B" --compose-file "$ROOT/compose.yaml:$ROOT/compose.test.yaml" 2>/dev/null; then echo "FAIL: restore into a non-empty project must be refused"; exit 1; fi
echo "ok: second restore into the same project refused"

echo "== browser check against the restored stack"
cd "$ROOT/tests/e2e"; [ -d node_modules ] || pnpm install --frozen-lockfile
E2E_BASE_URL="http://localhost:$APP_PORT" pnpm exec playwright test specs/20-access.spec.ts -g "seeded demo data" --reporter=list
echo "BACKUP/RESTORE TEST PASSED"
