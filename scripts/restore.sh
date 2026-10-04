#!/bin/sh
# Restores a backup made by scripts/backup.sh into a NEW, EMPTY compose project (never into running production data).
#
#   scripts/restore.sh --from <backup dir> --project <new project> [--compose-file compose.yaml] [--env-file F]
#
# Safety: the script never deletes anything. It refuses to run if the target project already has volumes, and it
# refuses to use the project the backup was taken from. To replace a damaged production installation, first stop it and
# move its volumes away by hand (docs/operations/backup-restore.md), then restore into the freed project name.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
FROM=""; PROJECT=""; FILE="$ROOT/compose.yaml"; ENVFILE=""; ALLOW_SAME=0
while [ $# -gt 0 ]; do
  case "$1" in
    --from) FROM="$2"; shift 2 ;;
    --project) PROJECT="$2"; shift 2 ;;
    --compose-file) FILE="$2"; shift 2 ;;
    --env-file) ENVFILE="$2"; shift 2 ;;
    --allow-source-project-name) ALLOW_SAME=1; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$FROM" ] && [ -n "$PROJECT" ] || { echo "usage: restore.sh --from DIR --project NEWPROJECT" >&2; exit 2; }
[ -f "$FROM/manifest.json" ] || { echo "$FROM is not a backup directory" >&2; exit 1; }
( cd "$FROM" && sha256sum -c SHA256SUMS >/dev/null ) || { echo "backup checksum verification FAILED" >&2; exit 1; }
SRC="$(python3 -c "import json,sys; print(json.load(open('$FROM/manifest.json'))['source_project'])")"
if [ "$SRC" = "$PROJECT" ] && [ "$ALLOW_SAME" != "1" ]; then
  echo "refusing: '$PROJECT' is the project this backup was taken from. Pick a new project name (e.g. $PROJECT-restore)." >&2; exit 1
fi
if [ -n "$(docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT")" ]; then
  echo "refusing: project '$PROJECT' already has volumes. Restore never overwrites existing data." >&2; exit 1
fi
FILES=""; OLDIFS="$IFS"; IFS=:; for f in $FILE; do FILES="$FILES -f $f"; done; IFS="$OLDIFS"   # --compose-file accepts a colon separated list
C() { if [ -n "$ENVFILE" ]; then docker compose -p "$PROJECT" $FILES --env-file "$ENVFILE" "$@"; else docker compose -p "$PROJECT" $FILES "$@"; fi; }

echo "== starting empty database and storage"
C up -d --wait postgres storage
C up --no-deps --exit-code-from db-init db-init       # roles and empty databases (idempotent)
echo "== restoring PostgreSQL"
C exec -T postgres sh -c 'pg_restore -U postgres -d decision_evidence --no-owner --role=de_migrator --exit-on-error' < "$FROM/decision_evidence.dump"
C exec -T postgres sh -c 'pg_restore -U postgres -d keycloak --no-owner --role=keycloak --exit-on-error' < "$FROM/keycloak.dump"
echo "== restoring objects"
C run --rm --no-deps -u "$(id -u):$(id -g)" -v "$(cd "$FROM" && pwd):/backup:ro" api python -m decision_evidence.tools.storage_backup restore /backup
echo "== starting the stack (migrate is a no-op: the schema revision is part of the dump)"
C up -d --wait --wait-timeout 420
echo "== consistency check"
C run --rm --no-deps bootstrap python -m decision_evidence.tools.storage_backup verify
echo "restore complete into project $PROJECT"
