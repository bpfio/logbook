"""L-A1 数据层测试 (P0-1 去重 / P0-3 盖戳 / P0-4 search 去重 / P1-1 brief / P1-3 bulk / P1-4 LIMIT 钳制)

依赖本地 PG (docker logbook-postgres)，与 test_logbook.py 同环境。
所有写入均使用 l_a1_probe 项目隔离，收尾清理。
"""

import pytest
from logbook.db import db, MAX_LIMIT
from logbook.models import (
    Task, TaskStatus, Finding, FindingStatus, FindingSeverity,
    Waiting, WaitingStatus, DevLog,
)

PROJ = "l_a1_probe"


async def _cleanup():
    await db.ensure_project(PROJ)
    async with db._pool.acquire() as conn:
        await conn.execute(f'DELETE FROM "{PROJ}".task_timeline')
        await conn.execute(f'DELETE FROM "{PROJ}".tasks')
        await conn.execute(f'DELETE FROM "{PROJ}".findings')
        await conn.execute(f'DELETE FROM "{PROJ}".waitings')
        await conn.execute(f'DELETE FROM "{PROJ}".batches')
        await conn.execute("DELETE FROM shared.devlogs WHERE project_id = $1", PROJ)


@pytest.mark.asyncio
async def test_devlog_upsert_dedup_actions():
    """P0-1: 同 (project, task_id, title) 重复写入不产生新副本，action 标记正确。"""
    await _cleanup()
    base = dict(project_id=PROJ, task_id="F99", title="去重探针",
                problem="p1", root_cause="r1", solution="s1", evidence="e1")
    r1 = await db.record_devlog(DevLog(**base))
    assert r1.action == "created" and r1.id is not None

    # 完全重放 → cached
    r2 = await db.record_devlog(DevLog(**base))
    assert r2.action == "cached" and r2.id == r1.id

    # 四要素变更 → updated 且仍是同一行
    r3 = await db.record_devlog(DevLog(**{**base, "problem": "p2-changed"}))
    assert r3.action == "updated" and r3.id == r1.id
    assert r3.problem == "p2-changed"

    # task_id=NULL 也不塌缩: 不同 title 各自成行
    n1 = await db.record_devlog(DevLog(**{**base, "task_id": None, "title": "NULL任务键A"}))
    n2 = await db.record_devlog(DevLog(**{**base, "task_id": None, "title": "NULL任务键B"}))
    assert n1.action == "created" and n2.action == "created"
    n1b = await db.record_devlog(DevLog(**{**base, "task_id": None, "title": "NULL任务键A"}))
    assert n1b.action == "cached" and n1b.id == n1.id

    rows = await db._pool.fetch("SELECT id FROM shared.devlogs WHERE project_id = $1", PROJ)
    assert len(rows) == 3, "同键重放必须收敛为单副本"


@pytest.mark.asyncio
async def test_finding_fixed_resolved_at_stamp_preserved():
    """P0-3: findings 盖 resolved_at，重复 upsert 不刷新首戳；重开清除。"""
    await _cleanup()
    f = Finding(id="F-PROBE-1", source="test", severity=FindingSeverity.P2,
                status=FindingStatus.OPEN, summary="盖戳探针")
    saved = await db.upsert_finding(PROJ, f)
    assert saved.resolved_at is None

    saved = await db.upsert_finding(PROJ, saved.model_copy(update={"status": FindingStatus.FIXED}))
    assert saved.resolved_at is not None
    first_stamp = saved.resolved_at

    saved2 = await db.upsert_finding(PROJ, Finding(**saved.model_dump()))
    assert saved2.resolved_at == first_stamp, "首戳必须保留，不得被重复 upsert 刷新"

    # 重开 (fixed → open) → 戳清除
    saved3 = await db.upsert_finding(PROJ, saved2.model_copy(update={"status": FindingStatus.OPEN}))
    assert saved3.resolved_at is None


@pytest.mark.asyncio
async def test_waiting_closed_resolved_at_stamp():
    """P0-3: waitings closed 盖 resolved_at 且 COALESCE 保留首戳。"""
    await _cleanup()
    w = Waiting(id="W-PROBE-1", description="待办盖戳探针")
    saved = await db.upsert_waiting(PROJ, w)
    assert saved.resolved_at is None

    saved = await db.upsert_waiting(PROJ, saved.model_copy(update={"status": WaitingStatus.CLOSED}))
    assert saved.resolved_at is not None
    first = saved.resolved_at

    again = await db.upsert_waiting(PROJ, Waiting(**saved.model_dump()))
    assert again.resolved_at == first


@pytest.mark.asyncio
async def test_task_closed_at_no_drift_and_started_backfill():
    """P0-3: closed_at COALESCE 不漂移；planned→closed 直跳补打 started_at。"""
    await _cleanup()
    t = Task(id="T-PROBE-1", title="盖戳探针", status=TaskStatus.CLOSED,
             commit_hash="deadbee", batch_id=None)
    saved = await db.upsert_task(PROJ, t)
    assert saved.closed_at is not None and saved.started_at is not None, "直跳 closed 必须补打 started_at"
    first_closed = saved.closed_at

    # 重复 upsert (模型会重置 closed_at=now) → DB 层 COALESCE 保留首戳
    again = await db.upsert_task(PROJ, Task(**{**t.model_dump(), "closed_at": None}))
    assert again.closed_at == first_closed, "closed_at 不得被重复 upsert 刷新漂移"


@pytest.mark.asyncio
async def test_bulk_upsert_findings_and_waitings():
    """P1-3: bulk 单事务批量 upsert，盖戳语义一致。"""
    await _cleanup()
    fs = [Finding(id=f"F-BULK-{i}", source="test", severity=FindingSeverity.P3,
                  status=FindingStatus.OPEN, summary=f"bulk {i}") for i in range(5)]
    out = await db.bulk_upsert_findings(PROJ, fs)
    assert len(out) == 5 and all(o.id.startswith("F-BULK") for o in out)

    # 翻 fixed → 盖戳
    fs2 = [Finding(id=f"F-BULK-{i}", source="test", severity=FindingSeverity.P3,
                   status=FindingStatus.FIXED, summary=f"bulk {i}") for i in range(3)]
    out2 = await db.bulk_upsert_findings(PROJ, fs2)
    assert all(o.resolved_at is not None for o in out2)

    ws = [Waiting(id=f"W-BULK-{i}", description=f"wb {i}") for i in range(4)]
    wout = await db.bulk_upsert_waitings(PROJ, ws)
    assert len(wout) == 4
    ws2 = [Waiting(id=f"W-BULK-{i}", description=f"wb {i}", status=WaitingStatus.CLOSED) for i in range(4)]
    wout2 = await db.bulk_upsert_waitings(PROJ, ws2)
    assert all(o.resolved_at is not None for o in wout2)
    assert await db.bulk_upsert_findings(PROJ, []) == []
    assert await db.bulk_upsert_waitings(PROJ, []) == []


@pytest.mark.asyncio
async def test_brief_project_contract():
    """P1-1: brief_project 返回键固定 (跨线契约)。"""
    await _cleanup()
    await db.upsert_task(PROJ, Task(id="T-BR-1", title="open任务", status=TaskStatus.RUNNING))
    await db.upsert_task(PROJ, Task(id="T-BR-2", title="closed任务", status=TaskStatus.CLOSED,
                                    commit_hash="cafe123"))
    await db.upsert_finding(PROJ, Finding(id="F-BR-1", source="test", summary="open发现"))
    await db.upsert_waiting(PROJ, Waiting(id="W-BR-1", description="open待办"))
    await db.record_devlog(DevLog(project_id=PROJ, title="brief手记", problem="p",
                                  root_cause="r", solution="s", evidence="e"))

    brief = await db.brief_project(PROJ, devlog_limit=5)
    assert set(brief.keys()) == {
        "tasks_open", "findings_open", "waitings_open", "batch_running", "recent_devlogs"
    }
    assert [t["id"] for t in brief["tasks_open"]] == ["T-BR-1"]
    assert [f["id"] for f in brief["findings_open"]] == ["F-BR-1"]
    assert [w["id"] for w in brief["waitings_open"]] == ["W-BR-1"]
    assert brief["recent_devlogs"][0]["title"] == "brief手记"
    assert len(brief["recent_devlogs"]) == 1


@pytest.mark.asyncio
async def test_limit_clamped():
    """P1-4: LIMIT 钳制 ≤200 且参数化 (超限/非法值不炸、不放大)。"""
    assert db._clamp_limit(9999) == MAX_LIMIT == 200
    assert db._clamp_limit(0) == 50
    assert db._clamp_limit(-5) == 50
    assert db._clamp_limit(None) == 50
    assert db._clamp_limit("abc") == 50

    await _cleanup()
    # 查询面超限值不得产生 SQL 错误 (LIMIT 参数化)
    rows = await db.query_tasks(PROJ, limit=10**9)
    assert rows == []
    rows = await db.query_findings(PROJ, limit=10**9)
    assert rows == []
    rows = await db.query_waitings(PROJ, limit=10**9)
    assert rows == []
