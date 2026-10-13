#!/usr/bin/env bash
# Ephemeral PostgreSQL lifecycle for reproducible integration gates.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT/deploy/test/compose.yaml"
ACTION="${1:-status}"

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

preflight() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "ERROR: docker is required for integration tests." >&2
    exit 2
  fi
  if ! docker info >/dev/null 2>&1; then
    echo "ERROR: Docker daemon is unavailable or permission was denied." >&2
    exit 2
  fi
  if ! docker compose version >/dev/null 2>&1; then
    echo "ERROR: Docker Compose v2 is required." >&2
    exit 2
  fi
  if [ ! -f "$COMPOSE_FILE" ]; then
    echo "ERROR: missing tracked integration database definition: $COMPOSE_FILE" >&2
    exit 2
  fi
}

case "$ACTION" in
  start)
    preflight
    compose down -v --remove-orphans >/dev/null 2>&1 || true
    compose up -d --wait
    stable=0
    for _ in $(seq 1 120); do
      if compose exec -T postgres psql -U logbook -d logbook_test -Atqc \
        "SELECT to_regprocedure('shared.init_project_schema(text)') IS NOT NULL" \
        >/dev/null 2>&1; then
        stable=$((stable + 1))
        if [ "$stable" -ge 5 ]; then
          break
        fi
      else
        stable=0
      fi
      sleep 0.25
    done
    if [ "$stable" -lt 5 ]; then
      echo "ERROR: PostgreSQL did not remain ready after initialization." >&2
      compose logs --tail 120 postgres >&2 || true
      compose down -v --remove-orphans >/dev/null 2>&1 || true
      exit 2
    fi
    port="${LOGBOOK_TEST_DB_PORT:-15432}"
    echo "postgresql://logbook:test_only_password@127.0.0.1:${port}/logbook_test"
    ;;
  stop)
    if command -v docker >/dev/null 2>&1; then
      compose down -v --remove-orphans >/dev/null 2>&1 || true
    fi
    ;;
  status)
    compose ps
    ;;
  *)
    echo "Usage: $0 {start|stop|status}" >&2
    exit 2
    ;;
esac
