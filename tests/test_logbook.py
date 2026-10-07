"""Logbook 核心测试套件

测试项:
1. 闭环证据铁律校验 (未带 commit/proof 则拒绝 closed)
2. 敏感数据与凭据脱敏引擎
3. SNTP 漂移探针与北京时间格式化
4. 双平面 Schema 隔离与任务 CRUD
5. 排查手记录入与向量/全文混合检索
"""

import pytest
from pydantic import ValidationError
from logbook.models import (
    Task, TaskStatus, TaskPriority, TaskType,
    DevLog, DevLogVisibility
)
from logbook.sanitizer import sanitize_text
from logbook.time_sync import check_clock_drift, format_beijing, get_beijing_now
from logbook.db import db
from logbook.vector import get_embedding


def test_task_closed_invariant_fails_without_proof():
    """断言：若未提供 commit_hash 或 proof_link，则禁止将状态置为 closed。"""
    with pytest.raises(ValueError, match="闭环铁律"):
        Task(
            id="T01",
            title="测试任务",
            status=TaskStatus.CLOSED,
            commit_hash=None,
            proof_link=None,
        )


def test_task_closed_invariant_succeeds_with_proof():
    """断言：提供 commit_hash 后状态机成功置为 closed 并自适应补齐时间。"""
    task = Task(
        id="T02",
        title="测试任务2",
        status=TaskStatus.CLOSED,
        commit_hash="a1b2c3d4",
    )
    assert task.status == TaskStatus.CLOSED
    assert task.closed_at is not None


def test_sanitizer_masks_credentials_and_private_ips():
    """断言：脱敏引擎自动将私有 IP 转为 RFC 5737 占位符，且对 Token 掩码。"""
    dirty = "服务器位于 192.168.1.100 和 10.0.0.5，Token 为 ghp_1234567890abcdef1234567890abcdef1234"
    res = sanitize_text(dirty)
    assert "192.168.1.100" not in res.clean_text
    assert "10.0.0.5" not in res.clean_text
    assert "ghp_" not in res.clean_text
    assert "192.0.2.x" in res.clean_text
    assert "[REDACTED_GITHUB_TOKEN]" in res.clean_text
    assert res.redacted_count == 3


def test_sntp_clock_drift_check():
    """断言：SNTP 探针能够正常运行并返回漂移报告。"""
    rep = check_clock_drift(max_allowed_drift=5.0)
    assert rep.drift_seconds is not None
    assert isinstance(rep.is_synchronized, bool)


@pytest.mark.asyncio
async def test_database_crud_and_duration():
    """测试任务生命周期、用时自动度量、层级解耦与时间线记录。"""
    await db.ensure_project("test_proj")

    # 1. 登记父级 running 任务
    t = Task(
        id="TEST-01",
        title="性能调优主任务",
        status=TaskStatus.RUNNING,
        task_type=TaskType.FIX,
        priority=TaskPriority.P1,
        assignee="agy-planner",
        tags=["perf", "kernel"]
    )
    saved = await db.upsert_task("test_proj", t)
    assert saved.status == TaskStatus.RUNNING
    assert saved.started_at is not None
    assert saved.assignee == "agy-planner"
    assert "perf" in saved.tags

    # 2. 登记子任务并校验 parent_id 关联
    sub_task = Task(
        id="TEST-01.1",
        parent_id="TEST-01",
        title="子任务：内存配额调整",
        status=TaskStatus.PLANNED,
        task_type=TaskType.OPS,
        priority=TaskPriority.P2,
        assignee="agy-coder",
        tags=["memory", "docker"]
    )
    saved_sub = await db.upsert_task("test_proj", sub_task)
    assert saved_sub.parent_id == "TEST-01"
    assert saved_sub.assignee == "agy-coder"

    # 查询子任务
    queried_subs = await db.query_tasks("test_proj", parent_id="TEST-01")
    assert len(queried_subs) == 1
    assert queried_subs[0].id == "TEST-01.1"

    # 3. 闭环主任务并验证耗时度量
    saved.status = TaskStatus.CLOSED
    saved.commit_hash = "commit_deadbeef"
    saved = Task(**saved.model_dump())  # 触发 Pydantic 校验
    closed_task = await db.upsert_task("test_proj", saved)
    assert closed_task.status == TaskStatus.CLOSED
    assert closed_task.closed_at is not None
    assert closed_task.duration_seconds is not None


@pytest.mark.asyncio
async def test_devlog_record_and_search():
    """测试根因四要素手记录入与向量/全文检索。"""
    vec_res = await get_embedding("xdp reconcile EBUSY bpf_link")
    assert vec_res.embedding is not None
    assert len(vec_res.embedding) == 512

    devlog = DevLog(
        project_id="test_proj",
        task_id="TEST-01",
        title="xdp reconcile EBUSY 机理修复",
        problem="重启网卡报 EBUSY 设备忙",
        root_cause="bpf_link 残留导致互斥",
        solution="引入 SessionXDPProgIDs 豁免集",
        evidence="实测网卡热重载通过 0 报错",
        visibility=DevLogVisibility.PUBLIC_SAFE,
        embedding=vec_res.embedding
    )
    saved_log = await db.record_devlog(devlog)
    assert saved_log.id is not None

    # 检索
    hits = await db.search_devlogs(
        project="test_proj",
        query_vector=vec_res.embedding,
        limit=3
    )
    assert len(hits) >= 1
    assert "EBUSY" in hits[0]["title"]
    assert hits[0]["score"] > 0.8
