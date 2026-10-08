"""F14 数据清洗 dry-run (只读): 列出 shared.devlogs 重复副本组与将删 id 清单.

正典: docs/PLAN_OPT_2026-10-08.md §一 P0-1 —
  "数据清洗: F14 ×4 副本去重脚本 (保留最新 vector, 旧副本删除+HNSW 索引自愈)"。

根因对照: 病1 写路径分裂 (import_brix.py 直调裸 INSERT 绕过去重) + 病2 幂等键塌缩
(task_id=None 查重退化 title 全等) → (project_id, task_id, title) 相同的多副本。

本脚本**只读**: 绝不执行 DELETE, 仅输出:
  - 重复副本组 (project_id, task_id, title) 及各副本 id/embedding/时间;
  - 每组保留 id (优先: embedding 非空且最新; 否则 id 最大) 与将删 id 清单;
  - 汇总将删行数 (供主控在 PG 备份后核对真实清理执行量)。

退出码: 0=无重复; 3=发现重复 (dry-run 提示态, 供门禁区分)。

用法:
  DATABASE_URL=postgresql://... .venv/bin/python scripts/dedup_devlogs_dryrun.py
"""

import os
import sys
import asyncio

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import asyncpg

DSN = os.getenv("DATABASE_URL")
if not DSN:
    DSN = (
        f"postgresql://{os.getenv('LOGBOOK_PG_USER', os.getenv('POSTGRES_USER', 'logbook'))}"
        f":{os.getenv('LOGBOOK_PG_PASSWORD', os.getenv('POSTGRES_PASSWORD', 'logbook_dev_secret'))}"
        f"@{os.getenv('LOGBOOK_PG_HOST', '127.0.0.1')}:{os.getenv('LOGBOOK_PG_PORT', '5432')}"
        f"/{os.getenv('LOGBOOK_PG_DB', os.getenv('POSTGRES_DB', 'logbook'))}"
    )


async def main() -> int:
    conn = await asyncpg.connect(DSN, timeout=10)
    try:
        rows = await conn.fetch("""
            SELECT id, project_id, task_id, title, author,
                       (embedding IS NOT NULL) AS has_vec,
                       occurred_at, created_at, updated_at
            FROM shared.devlogs
            WHERE (project_id, COALESCE(task_id, ''), title) IN (
                SELECT project_id, COALESCE(task_id, ''), title
                FROM shared.devlogs
                GROUP BY project_id, COALESCE(task_id, ''), title
                HAVING count(*) > 1)
            ORDER BY project_id, COALESCE(task_id, ''), title, id
        """)

        groups: dict[tuple, list] = {}
        for r in rows:
            groups.setdefault((r["project_id"], r["task_id"] or "", r["title"]), []).append(r)

        if not groups:
            total = await conn.fetchval("SELECT count(*) FROM shared.devlogs")
            print(f"无重复副本组 (shared.devlogs 共 {total} 行) — dry-run 通过, 退出码 0")
            return 0

        will_delete: list[int] = []
        print("=" * 78)
        print(f"  shared.devlogs 重复副本组 dry-run (只读, 不执行删除) — 共 {len(groups)} 组")
        print("=" * 78)
        for (proj, tid, title), members in groups.items():
            # 保留策略: 优先 embedding 非空且 updated_at 最新; 否则 id 最大
            with_vec = [m for m in members if m["has_vec"]]
            pool = with_vec or members
            keep = max(pool, key=lambda m: (m["updated_at"], m["id"]))
            drop = [m for m in members if m["id"] != keep["id"]]
            will_delete.extend(m["id"] for m in drop)

            print(f"\n[{proj}] task_id={tid or '(NULL)'}  {title}")
            print(f"  组内副本 {len(members)} 个:")
            for m in members:
                tag = " <= 保留 (最新 vector)" if m["id"] == keep["id"] else " -> 将删"
                vec = "vec" if m["has_vec"] else "no-vec"
                print(f"    id={m['id']:>6} {vec:7s} updated={m['updated_at']:%Y-%m-%d %H:%M} "
                      f"author={m['author']}{tag}")

        print("\n--- 汇总 ---")
        print(f"重复组: {len(groups)}  副本总数: {len(rows)}  将删 id 数: {len(will_delete)}")
        print(f"将删 id 清单: {sorted(will_delete)}")
        print("提醒: 真实清理由主控在 PG 备份后执行 (删除旧副本后 HNSW 索引自愈/重建索引)。")
        return 3
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
