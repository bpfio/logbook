"""Logbook 核心测试套件 (0.8.0 极简正典)

测试项:
1. DataRecord 核心模型高精度金额与业务日期自适应
2. 敏感数据与凭据脱敏引擎
3. SNTP 漂移探针与北京时间格式化
4. 通用事实记录生命周期与检索
"""

import pytest
from decimal import Decimal
from pydantic import ValidationError
from logbook.models import DataRecord, Project
from logbook.sanitizer import sanitize_text
from logbook.time_sync import check_clock_drift, format_beijing, get_beijing_now
from logbook.db import db
from logbook.vector import get_embedding


def test_datarecord_spine_and_ribs():
    """断言：DataRecord 强脊梁与软肋骨模型初始化无误差。"""
    rec = DataRecord(
        project_id="brix",
        dataset_code="sys_task",
        doc_no="T01",
        entity_name="测试任务",
        amount="1234.5678",
        status="running",
        payload={"priority": "P1", "assignee": "agy"}
    )
    assert rec.doc_no == "T01"
    assert rec.amount == Decimal("1234.5678")
    assert rec.status == "running"
    assert rec.payload["priority"] == "P1"
    assert rec.created_at is not None


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
@pytest.mark.integration
async def test_database_crud_and_duration():
    """测试通用事实记录生命周期、用时自动度量与层级解耦。"""
    await db.ensure_project("test_proj")

    rec = DataRecord(
        project_id="test_proj",
        dataset_code="sys_task",
        doc_no="TEST-01",
        entity_name="性能调优主任务",
        status="running",
        payload={"task_type": "fix", "priority": "P1", "assignee": "agy-planner", "tags": ["perf", "kernel"]}
    )
    saved = await db.upsert_record(rec)
    assert saved["doc_no"] == "TEST-01"

    sub_rec = DataRecord(
        project_id="test_proj",
        dataset_code="sys_task",
        doc_no="TEST-01.1",
        parent_id=saved["id"],
        entity_name="子任务：内存配额调整",
        status="planned",
        payload={"task_type": "ops", "priority": "P2", "assignee": "agy-coder"}
    )
    saved_sub = await db.upsert_record(sub_rec)
    assert saved_sub["parent_id"] == saved["id"]

    queried_subs = await db.query_records("test_proj", dataset_code="sys_task", parent_id=saved["id"])
    assert len(queried_subs) == 1
    assert queried_subs[0].doc_no == "TEST-01.1"


@pytest.mark.asyncio
@pytest.mark.integration
async def test_devlog_record_and_search():
    """测试根因四要素手记录入与向量/全文检索。"""
    vec_res = await get_embedding("xdp reconcile EBUSY bpf_link")
    assert vec_res.embedding is not None
    assert len(vec_res.embedding) == 768

    rec = DataRecord(
        project_id="test_proj",
        dataset_code="sys_devlog",
        doc_no="DEV-01",
        entity_name="xdp reconcile EBUSY 机理修复",
        payload={
            "task_id": "TEST-01",
            "problem": "重启网卡报 EBUSY 设备忙",
            "root_cause": "bpf_link 残留导致互斥",
            "solution": "引入 SessionXDPProgIDs 豁免集",
            "evidence": "实测网卡热重载通过 0 报错",
            "visibility": "public_safe",
        },
        embedding=vec_res.embedding
    )
    saved_log = await db.upsert_record(rec)
    assert saved_log["id"] is not None

    hits = await db.query_records(
        project_id="test_proj",
        dataset_code="sys_devlog",
        query_text="EBUSY",
        limit=3
    )
    assert len(hits) >= 1
    assert "EBUSY" in hits[0].entity_name
