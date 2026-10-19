#!/usr/bin/env python3
"""768 维向量回填与重算脚本 (Backfill Re-embedding Script)

用途:
  在数据库完成 12_upgrade_768_and_enrich_research.sql 迁移后，
  批量为 shared.devlogs 与 shared.researches 中 embedding IS NULL 的存量数据
  重新生成高质量的 768 维向量并落库。

用法:
  .venv/bin/python scripts/reembed_768.py            # 执行真实重算回填
  .venv/bin/python scripts/reembed_768.py --dry-run  # 仅统计待重算行数
"""

import os
import sys
import asyncio
import argparse

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import asyncpg
from logbook.vector import get_embedding

DSN = os.getenv("DATABASE_URL")
if not DSN:
    DSN = (
        f"postgresql://{os.getenv('LOGBOOK_PG_USER', os.getenv('POSTGRES_USER', 'logbook'))}"
        f":{os.getenv('LOGBOOK_PG_PASSWORD', os.getenv('POSTGRES_PASSWORD', 'logbook_dev_secret'))}"
        f"@{os.getenv('LOGBOOK_PG_HOST', '127.0.0.1')}:{os.getenv('LOGBOOK_PG_PORT', '5432')}"
        f"/{os.getenv('LOGBOOK_PG_DB', os.getenv('POSTGRES_DB', 'logbook'))}"
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description="768 维向量批量重算回填脚本")
    parser.add_argument("--dry-run", action="store_true", help="只读统计，不执行写入")
    args = parser.parse_args()

    conn = await asyncpg.connect(DSN, timeout=10)
    try:
        # 1. 扫描 shared.devlogs
        devlog_rows = await conn.fetch("""
            SELECT id, title, problem, root_cause, solution
            FROM shared.devlogs
            WHERE embedding IS NULL
            ORDER BY id ASC;
        """)

        # 2. 扫描 shared.researches
        research_rows = await conn.fetch("""
            SELECT id, title, doc_no, authority, objective, market_landscape, decision
            FROM shared.researches
            WHERE embedding IS NULL
            ORDER BY id ASC;
        """)

        print(f"[768 Backfill] 发现待重算 DevLogs: {len(devlog_rows)} 条, Researches: {len(research_rows)} 条")

        if args.dry_run:
            print("[768 Backfill] --dry-run 模式，未执行真实更新。")
            return 0

        # 执行 devlogs 回填
        dev_success = 0
        for r in devlog_rows:
            full_text = f"{r['title']} {r['problem']} {r['root_cause']} {r['solution']}"
            res = await get_embedding(full_text)
            if res.embedding:
                vec_literal = f"[{','.join(str(x) for x in res.embedding)}]"
                await conn.execute(
                    "UPDATE shared.devlogs SET embedding = $1::vector WHERE id = $2;",
                    vec_literal, r["id"]
                )
                dev_success += 1

        # 执行 researches 回填
        res_success = 0
        for r in research_rows:
            full_text = (
                f"{r['title']} {r['doc_no'] or ''} {r['authority'] or ''} "
                f"{r['objective']} {r['market_landscape']} {r['decision']}"
            )
            res = await get_embedding(full_text)
            if res.embedding:
                vec_literal = f"[{','.join(str(x) for x in res.embedding)}]"
                await conn.execute(
                    "UPDATE shared.researches SET embedding = $1::vector WHERE id = $2;",
                    vec_literal, r["id"]
                )
                res_success += 1

        print(f"[768 Backfill] 完成: DevLogs 成功 {dev_success}/{len(devlog_rows)}, Researches 成功 {res_success}/{len(research_rows)}")
        return 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
