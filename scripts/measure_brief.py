"""L-A4 波2 验证门: logbook_brief digest 单调用 token 度量 vs 逐查询拼全貌基线.

正典: docs/PLAN_OPT_2026-10-08.md §二 P1-1 / §四 波2 验证门 —
  "brief 单调用 token 计数实测 (目标 ≤2K, 对照 5-6K)"。

度量口径:
  token 近似 = len(chars) // 4 (tiktoken 可用时用 tiktoken 精确计)。
  基线 = task_query + finding_query + waiting_query + batch_query 四次调用
         响应文本顺序拼接的 token 数 (现状拼全貌 ≈5-6K token 且可能过期)。
  对照 = logbook_brief(project) 单次响应 token 数 (P1-1 落地后存在)。

连接形态: 直调 logbook.mcp_server 函数层 (与 tests/test_mcp.py 同形), 本地 dev PG
即可跑; 无需 spawn 子进程。logbook_brief 尚未落地 (P1-1 属波2) 时标记
NOT_AVAILABLE 并以基线数据 + 明确 skip 退出 (退出码 0); --strict 时 brief 缺席
即退出码 2, 供波2 验收门收紧。

用法:
  .venv/bin/python scripts/measure_brief.py [--project brix] [--strict] [--target 2000]
"""

import os
import sys
import json
import asyncio
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

try:
    import tiktoken  # type: ignore
except ImportError:
    tiktoken = None


def count_tokens(text: str) -> int:
    if tiktoken is not None:
        try:
            return len(tiktoken.get_encoding("cl100k_base").encode(text))
        except Exception:
            pass
    return len(text) // 4


def textify(obj) -> str:
    """与 MCP 传输面同口径: 函数层返回 dict, 序列化为 JSON 文本计量。"""
    if isinstance(obj, str):
        return obj
    return json.dumps(obj, ensure_ascii=False, default=str)


async def measure(project: str, strict: bool, target: int) -> int:
    import logbook.mcp_server as ms

    # ---- 基线: 四次查询拼全貌 ----
    parts: dict[str, str] = {}
    tq = await ms.task_query(project=project, limit=500)
    parts["task_query"] = textify(tq)
    fq = await ms.finding_query(project=project, limit=500)
    parts["finding_query"] = textify(fq)
    wq = await ms.waiting_query(project=project, status="open")
    parts["waiting_query"] = textify(wq)
    bq = await ms.batch_query(project=project, limit=50)
    parts["batch_query"] = textify(bq)

    baseline_text = "".join(parts.values())
    baseline_tok = count_tokens(baseline_text)

    # ---- 对照: logbook_brief 单调用 (P1-1 落地后存在) ----
    brief_fn = getattr(ms, "logbook_brief", None) or getattr(ms, "brief", None)
    brief_tok = None
    brief_note = ""
    if brief_fn is None:
        brief_note = "logbook_brief 工具尚未落地 (P1-1, 波2) — 本次仅测基线"
        if strict:
            print(f"FAIL: {brief_note} (--strict)")
            return 2
    else:
        br = await brief_fn(project=project)
        brief_tok = count_tokens(textify(br))

    # ---- 输出对比表 ----
    enc = f"tiktoken({tiktoken.__version__})" if tiktoken else "近似 len//4 (tiktoken 未安装)"
    print("=" * 78)
    print(f"  logbook_brief token 度量 — project={project}  计数口径: {enc}")
    print(f"  目标: brief ≤ {target} token (基线现状 ≈5-6K)")
    print("=" * 78)
    print(f"  {'度量项':<34s}{'字符数':>10s}{'token(近)':>12s}")
    print("  " + "-" * 56)
    for name, txt in parts.items():
        print(f"  {'基线·' + name:<34s}{len(txt):>10d}{count_tokens(txt):>12d}")
    print("  " + "-" * 56)
    print(f"  {'基线合计 (4 次调用拼全貌)':<32s}{len(baseline_text):>10d}{baseline_tok:>12d}")
    if brief_tok is not None:
        ratio = baseline_tok / brief_tok if brief_tok else float("inf")
        verdict = "达标" if brief_tok <= target else "未达标"
        print(f"  {'对照·logbook_brief (单调用)':<31s}{len(textify(br)):>10d}{brief_tok:>12d}")
        print(f"\n  结论: brief={brief_tok} tok vs 基线={baseline_tok} tok "
              f"(省 {100 - brief_tok * 100 // max(baseline_tok, 1)}%, {ratio:.1f}x) → 目标≤{target}: {verdict}")
        return 0 if brief_tok <= target else 1
    print(f"  {'对照·logbook_brief':<34s}{'-':>10s}{'SKIP':>12s}")
    print(f"\n  结论: {brief_note}; 基线实测={baseline_tok} tok (四调用拼全貌), 退出码 0 (skip)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="brief digest token 度量 vs 拼全貌基线 (L-A4)")
    ap.add_argument("--project", default=os.getenv("LOGBOOK_MEASURE_PROJECT", "brix"))
    ap.add_argument("--strict", action="store_true",
                    help="logbook_brief 未落地即退出码 2 (波2 验收门收紧用)")
    ap.add_argument("--target", type=int, default=2000, help="brief 单包 token 目标上限")
    args = ap.parse_args()
    return asyncio.run(measure(args.project, args.strict, args.target))


if __name__ == "__main__":
    sys.exit(main())
