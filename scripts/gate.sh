#!/usr/bin/env bash
# Clean-checkout quality gate: unit -> ephemeral PostgreSQL -> replay/measure.
# Modes: unit | integration | full (default)
set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONPATH="$REPO_ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
MODE="${1:-full}"
TEST_DB_STARTED=0

cd "$REPO_ROOT"

case "$MODE" in
  unit|integration|full) ;;
  *)
    echo "Usage: $0 [unit|integration|full]" >&2
    exit 2
    ;;
esac

if [ ! -x "$PY" ]; then
  echo "ERROR: Python interpreter is unavailable." >&2
  exit 2
fi

DEPENDENCY_PROBE='import pytest, pydantic, asyncpg, mcp, rich, httpx, asyncssh, click'
if ! "$PY" -c "$DEPENDENCY_PROBE" >/dev/null 2>&1; then
  if [ ! -x "$REPO_ROOT/.venv/bin/python" ] && command -v uv >/dev/null 2>&1; then
    echo "== Bootstrapping isolated development environment with uv"
    UV_CACHE_DIR="${TMPDIR:-/tmp}/logbook-uv-cache" uv venv "$REPO_ROOT/.venv" >/dev/null || exit 2
    UV_CACHE_DIR="${TMPDIR:-/tmp}/logbook-uv-cache" \
      uv pip install --python "$REPO_ROOT/.venv/bin/python" -e "${REPO_ROOT}[dev]" >/dev/null || exit 2
    PY="$REPO_ROOT/.venv/bin/python"
  fi
fi
if ! "$PY" -c "$DEPENDENCY_PROBE" >/dev/null 2>&1; then
  echo "ERROR: missing runtime/test dependencies; run: uv sync --extra dev" >&2
  exit 2
fi

for tracked in deploy/qnap/compose.yaml.example deploy/qnap/.env.example deploy/test/compose.yaml; do
  if [ ! -f "$tracked" ]; then
    echo "ERROR: required tracked gate input is missing: $tracked" >&2
    exit 2
  fi
done

declare -a NAMES RCS
overall=0
step_total=0

if [ "$MODE" = "unit" ]; then
  step_total=1
elif [ "$MODE" = "integration" ]; then
  step_total=4
else
  step_total=5
fi

run_step() {
  local name="$1"; shift
  NAMES+=("$name")
  echo ""
  echo "======================================================================"
  echo "== 门禁 $(( ${#NAMES[@]} ))/$step_total: $name"
  echo "======================================================================"
  "$@"; local rc=$?
  RCS+=("$rc")
  if [ "$rc" -ne 0 ] && [ "$rc" -ne 3 ]; then overall=1; fi
}

stop_test_db() {
  if [ "$TEST_DB_STARTED" -eq 1 ]; then
    "$REPO_ROOT/scripts/test_db.sh" stop >/dev/null 2>&1 || true
    TEST_DB_STARTED=0
  fi
}
trap stop_test_db EXIT

if [ "$MODE" = "unit" ] || [ "$MODE" = "full" ]; then
  run_step "pytest unit (-m 'not integration')" "$PY" -m pytest tests/ -q -m "not integration"
fi

if [ "$MODE" = "integration" ] || [ "$MODE" = "full" ]; then
  echo ""
  echo "== 启动隔离 PostgreSQL (deploy/test/compose.yaml)"
  DATABASE_URL="$("$REPO_ROOT/scripts/test_db.sh" start)"
  export DATABASE_URL
  export LOGBOOK_AGENT_ID="${LOGBOOK_AGENT_ID:-agy}"
  TEST_DB_STARTED=1
  echo "DATABASE_URL=$DATABASE_URL"

  run_step "pytest integration (-m integration)" "$PY" -m pytest tests/ -q -m integration
  run_step "replay_brix_writes.py (18 笔幂等重放)" "$PY" scripts/replay_brix_writes.py
  run_step "measure_brief.py (brief token 度量 vs 基线)" "$PY" scripts/measure_brief.py --project brix
  run_step "dedup_devlogs_dryrun.py (F14 清洗 dry-run, 只读)" "$PY" scripts/dedup_devlogs_dryrun.py
fi

echo ""
echo "======================================================================"
echo "== 门禁汇总"
echo "======================================================================"
for i in "${!NAMES[@]}"; do
  rc="${RCS[$i]}"
  case "$rc" in
    0) verdict="PASS" ;;
    3) verdict="DUP-FOUND (dry-run 提示态)" ;;
    *) verdict="FAIL (rc=$rc)" ;;
  esac
  printf '  [%s] %s\n' "$verdict" "${NAMES[$i]}"
done

if [ "$overall" -eq 0 ]; then
  echo "门禁: 全绿 (退出码 0)"
else
  echo "门禁: 存在失败 (退出码 1)"
fi
exit "$overall"
