"""L-A4 波1 验证门: 重放 2026-10-07/08 主控对 brix 项目的 18 笔真实写入 (幂等回归).

正典: docs/PLAN_OPT_2026-10-08.md §四 验证门 —
  "重放 2026-10-07/08 brix 18 笔写入 (幂等=零新副本)"。

18 笔构成 (payload 均提取自 /home/yupeng/brix/docs/DEVLOG.md 发现台账/待办台账/夜11夜12 批段):
  - F-243 立案 (open) + F-243 终局更新 (fixed)          x2
  - F-322-6 / F-320 / F-132-C / F-317 翻账               x4
  - F-322-5 infix 裁决落账 / F-321 wontfix 裁决落账      x2
  - DRILL-F15-04 ~ DRILL-F15-11                          x8
  - WAIT-F320 (closed) / N11-8810 (open) waiting_record  x2

幂等判据 (每笔调用两次):
  1. 第二次调用必须命中幂等路径 — devlog/finding 类响应含 cached/reused/dedup 标记,
     或同 id 返回且行数不增;
  2. 该 id 的副本数前后不变 (第一遍后 count == 第二遍后 count == 1, 即零新副本);
  3. 全部通过退出码 0, 任一失败退出码 1。

连接形态仿 scripts/mcp_ingest_brix.py (MCP stdio ClientSession), 但缺省本机
spawn `python -m logbook.mcp_server` 子进程 (连本地 dev PG, DATABASE_URL 可覆盖);
禁 ssh 缺省关闭, --ssh 显式开启时才走远端管道 (供真机 QNAP 回归用)。

用法:
  .venv/bin/python scripts/replay_brix_writes.py             # 本地 MCP stdio 重放
  LOGBOOK_MCP_SSH=host .venv/bin/python scripts/replay_brix_writes.py --ssh
"""

import os
import sys
import json
import asyncio
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

REPORTER = "agy"
PROJECT = "brix"

# ---------------------------------------------------------------- 18 笔真实 payload
FINDING_WRITES = [
    # --- 夜11: F-243 立案 (DEVLOG.md 发现台账 F-243 / F243-file 行) ---
    {
        "id": "F-243",
        "phase": "立案",
        "payload": {
            "project": PROJECT, "id": "F-243",
            "source": "realmachine", "severity": "P1", "status": "open",
            "task_id": "F243-file", "reporter": REPORTER,
            "summary": "DHCPv6 池/前缀 rebatch 生命周期缺口 (advertise 无 IA_NA, LAN v6 代际失效) — P1 下批头件; 修向①池随 rebatch 重推导②flare prefixFn 同源③advertise 无址显式日志",
        },
    },
    # --- 夜11 S4-A11 后 F-243 终局更新 (fixed, 同 id 翻状态) ---
    {
        "id": "F-243",
        "phase": "终局",
        "payload": {
            "project": PROJECT, "id": "F-243",
            "source": "realmachine", "severity": "P1", "status": "fixed",
            "task_id": "N11-F243R", "reporter": REPORTER,
            "resolution": "4c12206",
            "summary": "DHCPv6 rebatch 生命周期缺口 — 根因=seed 段被选段层 D1 双排除+retire 静默; 根修①②③活体实证 (applied seed_share=true/br-lan 新代 2b75/flare ACTIVE); 残余=88.10 renew6 用户侧动作/F-176 凭据 (WAIT-11)",
        },
    },
    # --- 翻账 x4 ---
    {
        "id": "F-322-6", "phase": "翻账",
        "payload": {
            "project": PROJECT, "id": "F-322-6",
            "source": "F-322池", "severity": "P3", "status": "fixed",
            "task_id": "F14", "reporter": REPORTER, "resolution": "F14",
            "summary": "归因收敛: 管理面豁免无代码对应, 成功投递=journal 静默 (F14 机理调查定性翻案 99a779b), 两候选归一",
        },
    },
    {
        "id": "F-320", "phase": "翻账",
        "payload": {
            "project": PROJECT, "id": "F-320",
            "source": "realmachine", "severity": "P1", "status": "fixed",
            "task_id": "F-320-fix", "reporter": REPORTER, "resolution": "efcf2ac+8a49821",
            "summary": "上游 14B 截断 — 三选一落定组合案: clamp 止血 (真机现挂 iptables mangle TCPMSS 1412) + MRU 接线已落库 (network.wanN.pppoe.{mru,mss_clamp_size} 全链, 真机验收=N11-MRUVERIFY) / 上游工单归用户",
        },
    },
    {
        "id": "F-132-C", "phase": "翻账",
        "payload": {
            "project": PROJECT, "id": "F-132-C",
            "source": "realmachine", "severity": "P2", "status": "fixed",
            "reporter": REPORTER, "resolution": "T10-活体复核",
            "summary": "F-132-C 闭环翻账: sockops 活体在载 48h 零拒载 (夜12 T10 旧池活体复核), eBPF sockops 加速链确认闭环",
        },
    },
    {
        "id": "F-317", "phase": "翻账",
        "payload": {
            "project": PROJECT, "id": "F-317",
            "source": "realmachine", "severity": "P2", "status": "fixed",
            "reporter": REPORTER, "resolution": "T10-活体复核",
            "summary": "F-317 闭环 (夜12 T10 旧池活体复核): 复检项活体通过, 闭环翻账",
        },
    },
    # --- 裁决落账 x2 ---
    {
        "id": "F-322-5", "phase": "裁决落账",
        "payload": {
            "project": PROJECT, "id": "F-322-5",
            "source": "F-322池", "severity": "P2", "status": "infix",
            "reporter": REPORTER, "task_id": "T2",
            "summary": "F-322⑤ 定性翻案: 管理面告警豁免无代码对应, 成功投递=journal 静默 — 主控裁 10-08: 做 (wan 物理口抖动显式告警, T2 施工中, 铁律\"劣化不静默\")",
        },
    },
    {
        "id": "F-321", "phase": "裁决落账",
        "payload": {
            "project": PROJECT, "id": "F-321",
            "source": "realmachine", "severity": "P2", "status": "wontfix",
            "reporter": REPORTER,
            "summary": "主控裁 10-08: ISP 无 PD 外部现实, RA-PIO/seed 即供给 (r170 活体), 不引入新机制",
        },
    },
    # --- DRILL-F15-04 ~ 11 x8 (F19 停机转下批, WIP @ origin/night10/cli-fix-19) ---
    *[
        {
            "id": f"DRILL-F15-{n:02d}", "phase": "翻账",
            "payload": {
                "project": PROJECT, "id": f"DRILL-F15-{n:02d}",
                "source": "drill-F15", "severity": "P3", "status": "fixed",
                "task_id": "F19", "reporter": REPORTER, "resolution": "F19/a2fb6c7",
                "summary": "F19 停机转下批 — WIP 在 origin/night10/cli-fix-19",
            },
        }
        for n in range(4, 12)
    ],
]

WAITING_WRITES = [
    {
        "id": "WAIT-F320", "phase": "closed",
        "payload": {
            "project": PROJECT, "id": "WAIT-F320", "category": "user",
            "status": "closed", "owner": "user",
            "description": "F-320 处置三选一 ✅ 组合案落定 efcf2ac",
            "resolution": "组合案落定 efcf2ac (clamp 止血 + MRU 接线码修 + 上游工单)",
        },
    },
    {
        "id": "N11-8810", "phase": "open",
        "payload": {
            "project": PROJECT, "id": "N11-8810", "category": "user",
            "status": "open", "owner": "user",
            "description": "88.10 (Windows) 跑 ipconfig /renew6 (或补 F-176 凭据自动化) → F-243 发布面闭环: 88.10 获新代 IA_NA → flare 学址发布 → v6.b3x.net 4539 旧死址替换 (码侧已就绪 r170)",
        },
    },
]


def _text(res) -> str:
    return res.content[0].text if res.content else ""


def _obj(res) -> dict:
    t = _text(res)
    try:
        return json.loads(t)
    except Exception:
        return {"_raw": t}


def _call_failed(res) -> bool:
    """MCP 传输面 is_error 或结构化错误体 (isError:true, P0-2 形态) 都算失败。"""
    if getattr(res, "is_error", False):
        return True
    return bool(_obj(res).get("isError"))


IDEMPOTENCY_MARKERS = ("cached", "reused", "dedup", "updated", "existing", "idempotent", "cached_skip")


async def count_findings(session, fid: str) -> int:
    res = await session.call_tool("finding_query", {"project": PROJECT, "limit": 500})
    obj = _obj(res)
    return sum(1 for it in obj.get("items", []) if it.get("id") == fid)


async def count_waitings(session, wid: str) -> int:
    # status=None 查全部 (缺省 status='open' 会漏掉 closed 项, WAIT-F320 判据需全态计数)
    res = await session.call_tool("waiting_query", {"project": PROJECT, "status": None, "limit": 500})
    obj = _obj(res)
    return sum(1 for it in obj.get("items", []) if it.get("id") == wid)


# 前置任务登记 (不计入 18 笔): 10-07/08 生产库存在这些任务, 本地 dev PG 台账子集缺;
# finding_record task_id 预检 (TASK_NOT_FOUND) 要求先登记 — 幂等 upsert, 无新行副作用。
PREREQ_TASKS = [
    {"project": PROJECT, "id": "F243-file", "title": "F-243 立案: DHCPv6 rebatch 生命周期缺口 (P1 下批头件)",
     "status": "closed", "task_type": "fix", "priority": "P1", "assignee": REPORTER,
     "proof_link": "brix/docs/DEVLOG.md#F243-file"},
    {"project": PROJECT, "id": "N11-F243R", "title": "F-243 根修①②③ (D1 双排除杀 seed 段→放行段+免排除+L=1+flare 同源+无址 WARN)",
     "status": "closed", "task_type": "fix", "priority": "P1", "assignee": REPORTER, "commit_hash": "4c12206"},
    {"project": PROJECT, "id": "F-320-fix", "title": "F-320 MSS clamp 止血 + MRU 接线组合案",
     "status": "closed", "task_type": "fix", "priority": "P1", "assignee": REPORTER, "commit_hash": "efcf2ac"},
    {"project": PROJECT, "id": "F19", "title": "DRILL-F15-04~11 残余修复 (WIP 停机保存)",
     "status": "closed", "task_type": "fix", "priority": "P3", "assignee": REPORTER, "commit_hash": "a2fb6c7"},
    {"project": PROJECT, "id": "T2", "title": "wan 物理口抖动显式告警 (F-322-5, 铁律劣化不静默)",
     "status": "running", "task_type": "fix", "priority": "P2", "assignee": REPORTER},
]


async def seed_prereq_tasks(session, results: list) -> set:
    """前置任务尽力登记 (幂等 upsert)。src/** 由他线占用, 若其瞬时故障导致
    某任务登记失败, 则对应 finding 剥离悬空 task_id 以脱离形态重放 (不计失败)。"""
    ok_ids: set = set()
    for t in PREREQ_TASKS:
        r = await session.call_tool("task_upsert", {**t, "allow_cross_project": True})
        o = _obj(r)
        ok = (not o.get("isError")) and (not getattr(r, "is_error", False))
        if ok:
            ok_ids.add(t["id"])
        results.append((f"prereq-task:{t['id']}", ok,
                        "登记成功 (幂等 upsert)" if ok else f"报错 (降级为剥离 task_id 重放): {_text(r)[:140]}"))
    return ok_ids


async def replay_finding(session, entry: dict, results: list, prereq_ok: set) -> None:
    fid = entry["id"]
    payload = {**entry["payload"], "allow_cross_project": True}  # 10-07/08 主控对 brix 真实调用形态 (跨项目显式授权)
    det = ""
    if payload.get("task_id") and payload["task_id"] not in prereq_ok:
        det = f" [task_id={payload['task_id']} 前置登记失败, 剥离悬空关联后重放]"
        payload.pop("task_id", None)
    before = await count_findings(session, fid)

    r1 = await session.call_tool("finding_record", payload)
    if _call_failed(r1):
        results.append((f"finding:{fid}:{entry['phase']}", False, f"第一次调用报错: {_text(r1)[:200]}"))
        return
    mid = await count_findings(session, fid)

    r2 = await session.call_tool("finding_record", payload)
    if _call_failed(r2):
        results.append((f"finding:{fid}:{entry['phase']}", False, f"第二次调用报错: {_text(r2)[:200]}"))
        return
    after = await count_findings(session, fid)

    o2 = _obj(r2)
    marker = any(k in json.dumps(o2, ensure_ascii=False).lower() for k in IDEMPOTENCY_MARKERS)
    det = det
    ok = (mid == 1) and (after == mid)  # 零新副本: 两遍后仍单行且行数不增
    ok = ok and (before <= 1)           # 基线本身无既有副本堆积 (首跑允许 before=0)
    detail = (f"id={fid} phase={entry['phase']} before={before} after1={mid} after2={after} "
              f"resp_marker={marker}{det}")
    if not marker and ok:
        # 行数判据已兜底 (upsert 同 id 单行), marker 仅作信息披露
        detail += " (响应未带显式幂等标记, 以副本数判据为准)"
    results.append((f"finding:{fid}:{entry['phase']}", ok, detail))


async def replay_waiting(session, entry: dict, results: list) -> None:
    wid = entry["id"]
    payload = {**entry["payload"], "allow_cross_project": True}  # 同上: 跨项目显式授权
    before = await count_waitings(session, wid)

    r1 = await session.call_tool("waiting_record", payload)
    if _call_failed(r1):
        results.append((f"waiting:{wid}", False, f"第一次调用报错: {_text(r1)[:200]}"))
        return
    mid = await count_waitings(session, wid)

    r2 = await session.call_tool("waiting_record", payload)
    if _call_failed(r2):
        results.append((f"waiting:{wid}", False, f"第二次调用报错: {_text(r2)[:200]}"))
        return
    after = await count_waitings(session, wid)

    ok = (mid == 1) and (after == mid) and (before <= 1)
    results.append((f"waiting:{wid}", ok,
                    f"id={wid} phase={entry['phase']} before={before} after1={mid} after2={after}"))


async def main() -> int:
    ap = argparse.ArgumentParser(description="brix 18 笔真实写入幂等重放 (L-A4 波1 验证门)")
    ap.add_argument("--ssh", action="store_true",
                    help="走 SSH 管道连远端生产 MCP (缺省本机 spawn stdio server, 禁 ssh 缺省)")
    ap.add_argument("--ssh-target", default=os.getenv("LOGBOOK_MCP_SSH", "sysadmin@192.168.1.68"))
    args = ap.parse_args()

    if args.ssh:
        server_params = StdioServerParameters(
            command="ssh",
            args=["-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=no", args.ssh_target, "mcp"],
            env=None,
        )
    else:
        server_params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "logbook.mcp_server"],
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )

    print("=" * 78)
    print(f"  brix 18 笔真实写入幂等重放 (findings={len(FINDING_WRITES)} waitings={len(WAITING_WRITES)})")
    print(f"  通道: {'ssh:' + args.ssh_target if args.ssh else '本机 stdio: python -m logbook.mcp_server'}")
    print("=" * 78)

    results: list[tuple[str, bool, str]] = []
    async with stdio_client(server_params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name for t in (await session.list_tools()).tools}
            print(f"[MCP] 握手成功, 工具 {len(tools)} 个; 需要 finding_record/waiting_record/"
                  f"finding_query/waiting_query: "
                  f"{all(t in tools for t in ('finding_record', 'waiting_record', 'finding_query', 'waiting_query'))}")

            prereq_ok = await seed_prereq_tasks(session, results)

            for entry in FINDING_WRITES:
                await replay_finding(session, entry, results, prereq_ok)
            for entry in WAITING_WRITES:
                await replay_waiting(session, entry, results)

    print("\n--- 逐笔判定 ---")
    failed = 0
    for name, ok, detail in results:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {name:34s} {detail}")
        if not ok:
            failed += 1

    print(f"\n总计 {len(results)} 笔, 通过 {len(results) - failed}, 失败 {failed}")
    print("幂等验证: " + ("通过 (退出码 0)" if failed == 0 else "未通过 (退出码 1)"))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
