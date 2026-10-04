#!/bin/sh
# Shared helpers for the test scripts. Safety rule: the teardown only ever removes projects named de-test-*.
set -eu

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

new_test_project() {
  # random suffix keeps parallel and repeated runs isolated (separate volumes, networks, ports)
  printf 'de-test-%s' "$(od -An -N4 -tx1 /dev/urandom | tr -d ' \n')"
}

free_port() {
  python3 - <<'PY'
import socket
s = socket.socket()
s.bind(("127.0.0.1", 0))
print(s.getsockname()[1])
s.close()
PY
}

# OLLAMA_OVERLAY=1 additionally starts the Ollama container of compose.ollama.yaml (see docs/operations/ollama.md)
compose_files() {
  printf '%s' "-f $ROOT/compose.yaml -f $ROOT/compose.test.yaml"
  [ "${OLLAMA_OVERLAY:-0}" = "1" ] && printf '%s' " -f $ROOT/compose.ollama.yaml"
  return 0
}

compose() {
  # shellcheck disable=SC2046
  docker compose -p "$TEST_PROJECT" $(compose_files) "$@"
}

teardown_test_project() {
  case "$TEST_PROJECT" in
    de-test-*) ;;
    *) echo "refusing to remove project '$TEST_PROJECT': not an isolated test project" >&2; return 1 ;;
  esac
  [ "${KEEP_TEST_PROJECT:-0}" = "1" ] && { echo "keeping project $TEST_PROJECT (KEEP_TEST_PROJECT=1)"; return 0; }
  compose down -v --remove-orphans >/dev/null 2>&1 || true
}
