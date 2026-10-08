#!/usr/bin/env bash
# L-A4 回归重放与度量 Harness 门禁 (docs/PLAN_OPT_2026-10-08.md §四 验证门)
# 顺序: pytest 单测全量 → 18 笔幂等重放 → brief token 度量 → F14 清洗 dry-run
# 退出码: 0=全绿; 1=pytest/重放失败; 3=发现重复副本 (dry-run 提示态, 非脚本错误)
set -u
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$REPO_ROOT/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3)"
cd "$REPO_ROOT"

declare -a NAMES RCS
overall=0
run_step() { # run_step <name> <cmd...>
  local name="$1"; shift
  NAMES+=("$name")
  echo ""
  echo "======================================================================"
  echo "== 门禁 $(( ${#NAMES[@]} ))/4: $name"
  echo "======================================================================"
  "$@"; local rc=$?
  RCS+=("$rc")
  if [ "$rc" -ne 0 ]; then overall=1; fi
}

run_step "pytest tests/ -q" "$PY" -m pytest tests/ -q
run_step "replay_brix_writes.py (18 笔幂等重放)" "$PY" scripts/replay_brix_writes.py
run_step "measure_brief.py (brief token 度量 vs 基线)" "$PY" scripts/measure_brief.py --project brix
run_step "dedup_devlogs_dryrun.py (F14 清洗 dry-run, 只读)" "$PY" scripts/dedup_devlogs_dryrun.py

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
