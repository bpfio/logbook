"""存量项目多用户权限回填脚本 (0.5.0, 运行于系统面/owner 身份)。

用途: 将存量库平滑迁入分组权限语义 (组内互见互写, 跨组不可见)。
- 未回填的项目保持 legacy 开放态 (全体账户可见可写), 零破坏;
- 回填后项目挂载到群组, 仅组成员可见可写, 立即收口。

用法:
  # 1. 指定 admin 账户 (缺省 agy, 或环境变量 LOGBOOK_ADMIN_AGENTS)
  python scripts/backfill_permissions.py --discover --admin agy

  # 2. 手动模式: 单项目挂组
  python scripts/backfill_permissions.py --project brix --group brix-team --agents agy,codebuddy,zcode
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from logbook.db import db  # noqa: E402


async def discover_participants(schema: str) -> list[str]:
    """从项目数据面收集参与过的账户 (tasks assignee / devlogs / messages / researches author)。"""
    agents: set[str] = set()
    spool = await db._acquire_system()
    async with spool.acquire() as conn:
        exists = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = $1)", schema
        )
        if not exists:
            return []
        for col, table in (("assignee", "tasks"), ("reviewer", "tasks")):
            rows = await conn.fetch(
                f'SELECT DISTINCT {col} AS a FROM "{schema}".{table} '
                f"WHERE {col} IS NOT NULL AND {col} <> ''"
            )
            agents.update(r["a"] for r in rows)
        for sql, args in (
            ("SELECT DISTINCT author AS a FROM shared.devlogs WHERE project_id = $1", (schema,)),
            ("SELECT DISTINCT author AS a FROM shared.researches WHERE project_id = $1", (schema,)),
            ("SELECT DISTINCT from_agent AS a FROM shared.agent_messages WHERE project_id = $1", (schema,)),
            ("SELECT DISTINCT to_agent AS a FROM shared.agent_messages WHERE project_id = $1", (schema,)),
        ):
            try:
                rows = await conn.fetch(sql, *args)
                agents.update(r["a"] for r in rows)
            except Exception:
                continue
    return sorted(a for a in agents if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", a))


async def backfill_project(schema: str, group_name: str, agents: list[str], operator: str) -> dict:
    await db.create_group(group_name, f"回填群组: 项目 {schema} 的历史参与账户", operator)
    added = []
    for a in agents:
        try:
            await db.add_group_member(group_name, a, operator)
            added.append(a)
        except ValueError as e:
            print(f"  [skip] 账户 [{a}] 非法: {e}")
    await db.grant_group_project(group_name, schema, operator)
    await db.reset_data_pool()
    return {"project": schema, "group": group_name, "members": added}


async def main() -> int:
    parser = argparse.ArgumentParser(description="Logbook 多用户权限存量回填")
    parser.add_argument("--discover", action="store_true", help="自动发现全部存量项目并按 <schema>-team 挂组")
    parser.add_argument("--project", help="手动模式: 目标项目 schema 名")
    parser.add_argument("--group", help="手动模式: 群组名")
    parser.add_argument("--agents", help="手动模式: 逗号分隔成员账户")
    parser.add_argument("--admin", help="确保该账户为 admin (可逗号分隔多个)")
    parser.add_argument("--dry-run", action="store_true", help="仅打印计划, 不落库")
    args = parser.parse_args()

    admin_env = [a.strip() for a in os.getenv("LOGBOOK_ADMIN_AGENTS", "").split(",") if a.strip()]
    if args.admin:
        admin_env.extend(a.strip() for a in args.admin.split(",") if a.strip())
    if not admin_env:
        admin_env = ["agy"]

    if not args.discover and not (args.project and args.group):
        parser.error("需要 --discover 或 --project + --group")

    await db.ensure_acl_bootstrap()
    for a in dict.fromkeys(admin_env):
        info = await db.ensure_principal(a, is_admin=True)
        print(f"[admin] 账户 [{info['agent_name']}] is_admin={info['is_admin']}")

    plans: list[tuple[str, str, list[str]]] = []
    if args.discover:
        projects = await db.list_projects()
        for p in projects:
            agents = await discover_participants(p.schema_name)
            plans.append((p.schema_name, f"{p.schema_name}-team", agents))
    else:
        agents = [a.strip() for a in (args.agents or "").split(",") if a.strip()]
        plans.append((args.project, args.group, agents))

    rc = 0
    for schema, group, agents in plans:
        if not agents:
            print(f"[skip] 项目 [{schema}] 未发现任何历史参与账户")
            continue
        print(f"[plan] 项目 [{schema}] -> 群组 [{group}] 成员: {agents}")
        if args.dry_run:
            continue
        result = await backfill_project(schema, group, agents, operator=admin_env[0])
        print(f"[done] {result}")
    return rc


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
