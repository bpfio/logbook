"""Logbook MCP 2.x 全维 Resources 直读与 Prompts 规程模板测试套件

覆盖:
1. 8 大 MCP Resources (只读上下文面) 渲染与数据驱动契约
2. 4 大 MCP Prompts (规程模板面) 纪律断言
3. 信箱待收 ID 索引直出与 message_read 引导提示
"""

import pytest
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone
from logbook.mcp_server import (
    get_active_tasks_resource,
    get_open_waitings_resource,
    get_active_leases_resource,
    get_recent_researches_resource,
    get_mailbox_summary_resource,
    get_projects_resource,
    get_engineering_rules_resource,
    get_schema_fields_resource,
    prompt_start_batch,
    prompt_record_devlog,
    prompt_conduct_research,
    prompt_agent_collaborate,
)
from logbook.models import FileLease, Research, ResearchCategory, ResearchStatus


# =============================================================================
# 一、 MCP Prompts 规程模板测试
# =============================================================================

def test_prompt_conduct_research_five_elements_and_discipline():
    """验证技术调研规程模板强制注入五要素与架构铁律。"""
    p = prompt_conduct_research("bpfio/logbook", "AsyncSSH vs Paramiko")
    assert "bpfio/logbook" in p
    assert "AsyncSSH vs Paramiko" in p
    assert "禁止重复造轮子" in p
    assert "评估对抗成本" in p
    assert "0/1 与 1-100" in p
    assert "objective" in p
    assert "tradeoffs" in p
    assert "decision" in p
    assert "references" in p
    assert "research_record" in p


def test_prompt_agent_collaborate_workflow():
    """验证跨 Agent 协同规程模板强制排他锁、对讲信件与释放闭环。"""
    p = prompt_agent_collaborate("bpfio/logbook", "src/logbook/db.py", "zcode")
    assert "bpfio/logbook" in p
    assert "src/logbook/db.py" in p
    assert "zcode" in p
    assert "lease_acquire" in p
    assert "message_send" in p
    assert "lease_release" in p


def test_existing_prompts_intact():
    """验证既有规程模板 (start_batch, record_devlog) 保持完好。"""
    p1 = prompt_start_batch("logbook", "DEV-01")
    assert "AGENTS.md" in p1
    assert "task_upsert" in p1

    p2 = prompt_record_devlog("logbook", "T101")
    assert "故障现象 (Problem)" in p2
    assert "根因定位 (Root Cause)" in p2
    assert "解决方案 (Solution)" in p2
    assert "验证证据 (Evidence)" in p2


# =============================================================================
# 二、 MCP Resources 只读上下文直挂测试
# =============================================================================

@pytest.mark.asyncio
async def test_get_active_leases_resource_empty_and_populated():
    """测试活跃租约看板只读挂载 (logbook://{project}/leases/active)。"""
    with patch("logbook.mcp_server.validate_and_negotiate_project", new=AsyncMock(return_value="bpfio/logbook")):
        # 1. 空租约场景
        with patch("logbook.mcp_server.db.query_file_leases", new=AsyncMock(return_value=[])):
            out_empty = await get_active_leases_resource("bpfio/logbook")
            assert "当前无活跃文件租约" in out_empty

        # 2. 存在活跃租约场景
        now = datetime.now(timezone.utc)
        fake_lease = FileLease(
            id=1,
            project_id="bpfio/logbook",
            agent_name="codebuddy",
            agent_ip="192.168.1.68",
            file_path="src/logbook/db.py",
            lease_expires_at=now,
            created_at=now
        )
        with patch("logbook.mcp_server.db.query_file_leases", new=AsyncMock(return_value=[fake_lease])):
            out_populated = await get_active_leases_resource("bpfio/logbook")
            assert "# 项目 [bpfio/logbook] 活跃文件租约看板" in out_populated
            assert "src/logbook/db.py" in out_populated
            assert "codebuddy" in out_populated
            assert "192.168.1.68" in out_populated


@pytest.mark.asyncio
async def test_get_recent_researches_resource_empty_and_populated():
    """测试最新技术调研快照只读挂载 (logbook://{project}/researches/recent)。"""
    with patch("logbook.mcp_server.validate_and_negotiate_project", new=AsyncMock(return_value="bpfio/logbook")):
        # 1. 空记录场景
        with patch("logbook.mcp_server.db.query_researches", new=AsyncMock(return_value=[])):
            out_empty = await get_recent_researches_resource("bpfio/logbook")
            assert "当前暂无技术调研" in out_empty

        # 2. 存在调研记录场景
        now = datetime.now(timezone.utc)
        fake_research = Research(
            id=10,
            project_id="bpfio/logbook",
            title="PostgreSQL 18 vs SQLite 并发评测",
            category=ResearchCategory.ARCHITECTURE,
            status=ResearchStatus.COMPLETED,
            author="agy",
            agent_ip="192.168.1.68",
            objective="评估低内存边缘 NAS 下的数据库选型",
            market_landscape="SQLite 易写锁; PG18 支持高并发与原生向量",
            tradeoffs="PG18 需 48MB 常驻内存; SQLite 无法满足跨 Agent 并发写",
            decision="选用 PG18 容器化部署",
            references="https://postgresql.org",
            created_at=now,
            updated_at=now
        )
        with patch("logbook.mcp_server.db.query_researches", new=AsyncMock(return_value=[fake_research])):
            out_pop = await get_recent_researches_resource("bpfio/logbook")
            assert "架构调研与技术决策快照" in out_pop
            assert "PostgreSQL 18 vs SQLite 并发评测" in out_pop
            assert "选用 PG18 容器化部署" in out_pop
            assert "192.168.1.68" in out_pop


@pytest.mark.asyncio
async def test_get_mailbox_summary_resource_indexes_and_hints():
    """测试信箱未读看板直出待提取 ID 索引与引导 (logbook://{project}/mailbox/summary)。"""
    with patch("logbook.mcp_server.validate_and_negotiate_project", new=AsyncMock(return_value="bpfio/logbook")):
        # 1. 全已读场景
        with patch("logbook.mcp_server.db.get_mailbox_summary", new=AsyncMock(return_value=[])):
            out_empty = await get_mailbox_summary_resource("bpfio/logbook")
            assert "当前信箱全部已读" in out_empty

        # 2. 存在未读信件场景 (断言 #42, #41 ID 直出与 message_read 引导)
        now = datetime.now(timezone.utc)
        fake_summary = [
            {
                "to_agent": "zcode",
                "unread_count": 2,
                "unread_ids": [42, 41],
                "latest_at": now
            }
        ]
        with patch("logbook.mcp_server.db.get_mailbox_summary", new=AsyncMock(return_value=fake_summary)):
            out_pop = await get_mailbox_summary_resource("bpfio/logbook")
            assert "对讲信箱未读看板" in out_pop
            assert "zcode" in out_pop
            assert "#42, #41" in out_pop
            assert "message_read(message_id=...)" in out_pop


@pytest.mark.asyncio
async def test_schema_fields_resource_static():
    """测试数据字典与字段正典资源只读直出 (logbook://schema/fields)。"""
    out = await get_schema_fields_resource()
    assert "Logbook 工业级研发台账数据字典与字段正典" in out
    assert "tasks" in out
    assert "devlogs" in out
    assert "findings" in out
