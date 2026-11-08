"""FastMCP 工具集端到端测试套件 (0.8.0 核心 14 工具契约与自愈能力)"""

import pytest
import os
from logbook.mcp_server import (
    record_upsert,
    records_bulk_upsert,
    record_query,
    record_search,
    record_stats,
    dataset_manage,
    message_send,
    message_inbox,
    message_action,
    lease_manage,
    group_manage,
    project_manage,
    brief,
    export_markdown,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_mcp_record_flow():
    """测试核心工具 record_upsert 与 record_query。"""
    res = await record_upsert(
        project="logbook",
        dataset_code="sys_task",
        doc_no="MCP-01",
        entity_name="测试 MCP 任务流水",
        status="running",
        payload={
            "priority": "P1",
            "task_type": "feat",
            "assignee": "agy-lead",
            "tags": ["core", "mcp"],
        }
    )
    assert res["success"] is True
    assert res["record"]["status"] == "running"
    assert res["record"]["doc_no"] == "MCP-01"

    # 查询
    q_res = await record_query(
        project="logbook",
        dataset_code="sys_task",
        doc_no="MCP-01"
    )
    assert q_res["success"] is True
    assert q_res["total"] >= 1
    assert q_res["items"][0]["doc_no"] == "MCP-01"


@pytest.mark.asyncio
async def test_mcp_brief_flow():
    """测试 logbook_brief 项目简报。"""
    b_res = await brief(project="logbook", devlog_limit=5)
    assert b_res["success"] is True
    assert "tasks_open" in b_res["brief"]
    assert len(b_res["text"]) > 0


@pytest.mark.asyncio
async def test_mcp_export_markdown_flow():
    """测试 export_markdown 结构化导出。"""
    exp_res = await export_markdown(project="logbook")
    assert exp_res["success"] is True
    assert "content" in exp_res
    assert len(exp_res["content"]) > 0
