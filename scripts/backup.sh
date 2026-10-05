#!/bin/sh
# Consistent backup of PostgreSQL (application DB + Keycloak DB) and the object storage of ONE compose project.
#
#   scripts/backup.sh --project <compose project> [--compose-file compose.prod.yaml[:overlay.yaml]] [--env-file .env.prod] [--out DIR] [--no-quiesce]
#
# Consistency: by default api, worker and outbox-publisher are stopped for the duration of the backup (a few seconds to
# minutes), so that no job, upload or deletion changes database and objects between the two copies. They are started again
# afterwards (also on failure). pg_dump itself is transactionally consistent. The script is read-only for all data; it only
# writes into the new backup directory and refuses to overwrite an existing one.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT="${COMPOSE_PROJECT_NAME:-}"; FILE="$ROOT/compose.yaml"; ENVFILE=""; OUT=""; QUIESCE=1
while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --compose-file) FILE="$2"; shift 2 ;;
    --env-file) ENVFILE="$2"; shift 2 ;;
    --out) OUT="$2"; shift 2 ;;
    --no-quiesce) QUIESCE=0; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$PROJECT" ] || { echo "--project is required" >&2; exit 2; }
OUT="${OUT:-$ROOT/backups/$PROJECT-$(date -u +%Y%m%dT%H%M%SZ)}"
[ ! -e "$OUT" ] || { echo "refusing to overwrite existing $OUT" >&2; exit 1; }
FILES=""; OLDIFS="$IFS"; IFS=:; for f in $FILE; do FILES="$FILES -f $f"; done; IFS="$OLDIFS"   # --compose-file accepts a colon separated list
C() { if [ -n "$ENVFILE" ]; then docker compose -p "$PROJECT" $FILES --env-file "$ENVFILE" "$@"; else docker compose -p "$PROJECT" $FILES "$@"; fi; }

mkdir -p "$OUT"
STOPPED=""
resume() { [ -n "$STOPPED" ] && C start $STOPPED >/dev/null 2>&1 || true; }
trap resume EXIT

if [ "$QUIESCE" = "1" ]; then
  STOPPED="api worker outbox-publisher"
  echo "== stopping $STOPPED for a consistent copy"
  C stop $STOPPED >/dev/null
fi

echo "== PostgreSQL"
C exec -T postgres sh -c 'pg_dump -U postgres -Fc --no-owner decision_evidence' > "$OUT/decision_evidence.dump"
C exec -T postgres sh -c 'pg_dump -U postgres -Fc --no-owner keycloak' > "$OUT/keycloak.dump"
REV="$(C exec -T postgres psql -U postgres -d decision_evidence -Atc 'select version_num from infra.alembic_version' | tr -d '\r')"

echo "== object storage"
C run --rm --no-deps -u "$(id -u):$(id -g)" -v "$OUT:/backup" api python -m decision_evidence.tools.storage_backup backup /backup

chmod -R go-rwx "$OUT"
( cd "$OUT" && sha256sum decision_evidence.dump keycloak.dump objects-manifest.json > SHA256SUMS )
cat > "$OUT/manifest.json" <<JSON
{"created_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)", "source_project": "$PROJECT", "alembic_revision": "$REV", "compose_file": "$(basename "$FILE")"}
JSON
echo "backup complete: $OUT (schema revision $REV)"
