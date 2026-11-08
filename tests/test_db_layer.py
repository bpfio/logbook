"""L-A1 数据层测试 (0.8.0 通用事实表 / P1-1 brief / P1-4 LIMIT 钳制)

依赖本地 PG，与 test_logbook.py 同环境。
所有写入均使用 l_a1_probe 项目隔离，收尾清理。
"""

import pytest
from decimal import Decimal
from logbook.db import db, MAX_LIMIT
from logbook.models import DataRecord

pytestmark = pytest.mark.integration

PROJ = "l_a1_probe"


async def _cleanup():
    await db.ensure_project(PROJ)
    async with db._pool.acquire() as conn:
        await conn.execute("DELETE FROM shared.records WHERE project_id = $1", PROJ)


@pytest.mark.asyncio
async def test_record_upsert_dedup_actions():
    """P0-1: 同 (project, dataset_code, doc_no) 复合主键冲突时自动更新，不产生孤儿副本。"""
    await _cleanup()
    rec1 = DataRecord(
        project_id=PROJ, dataset_code="sys_devlog", doc_no="DEV-99", entity_name="去重探针",
        status="recorded", payload={"problem": "p1", "solution": "s1"}
    )
    r1 = await db.upsert_record(rec1)
    assert r1["id"] is not None

    # 重复写入同一 doc_no
    rec2 = DataRecord(
        project_id=PROJ, dataset_code="sys_devlog", doc_no="DEV-99", entity_name="去重探针",
        status="recorded", payload={"problem": "p2-changed", "solution": "s1"}
    )
    r2 = await db.upsert_record(rec2)
    assert r2["id"] == r1["id"]

    # 查验单表总数仅为 1
    rows = await db.query_records(PROJ, dataset_code="sys_devlog", doc_no="DEV-99")
    assert len(rows) == 1
    assert rows[0].payload["problem"] == "p2-changed"


@pytest.mark.asyncio
async def test_brief_project_digest():
    """P1-1: brief_project 单次单表聚合返回 tasks_open, findings_open, waitings_open, batch_running, recent_devlogs。"""
    await _cleanup()
    await db.upsert_record(DataRecord(
        project_id=PROJ, dataset_code="sys_task", doc_no="T-BR-1", entity_name="open任务", status="running", amount=Decimal("0")
    ))
    await db.upsert_record(DataRecord(
        project_id=PROJ, dataset_code="sys_task", doc_no="T-BR-2", entity_name="closed任务", status="closed", amount=Decimal("0")
    ))
    await db.upsert_record(DataRecord(
        project_id=PROJ, dataset_code="sys_finding", doc_no="F-BR-1", entity_name="open发现", status="open", amount=Decimal("0")
    ))
    await db.upsert_record(DataRecord(
        project_id=PROJ, dataset_code="sys_waiting", doc_no="W-BR-1", entity_name="open待办", status="open", amount=Decimal("0")
    ))
    await db.upsert_record(DataRecord(
        project_id=PROJ, dataset_code="sys_devlog", doc_no="D-BR-1", entity_name="brief手记", status="recorded", amount=Decimal("0")
    ))

    data = await db.brief_project(PROJ, devlog_limit=5)
    assert len(data["tasks_open"]) == 1
    assert data["tasks_open"][0]["id"] == "T-BR-1"
    assert len(data["findings_open"]) == 1
    assert data["findings_open"][0]["id"] == "F-BR-1"
    assert len(data["waitings_open"]) == 1
    assert data["waitings_open"][0]["id"] == "W-BR-1"
    assert len(data["recent_devlogs"]) == 1
    assert data["recent_devlogs"][0]["title"] == "brief手记"


def test_clamp_limit_boundary():
    """P1-4: LIMIT 钳制上限 [1, MAX_LIMIT]。"""
    assert db._clamp_limit(None, default=50) == 50
    assert db._clamp_limit(-1, default=50) == 50
    assert db._clamp_limit(0, default=50) == 50
    assert db._clamp_limit(10, default=50) == 10
    assert db._clamp_limit(999999, default=50) == MAX_LIMIT
    assert db._clamp_limit("invalid", default=50) == 50
