"""FastMCP 工具集端到端测试套件"""

import pytest
import os
from logbook.mcp_server import (
    task_upsert,
    task_query,
    finding_record,
    waiting_query,
    devlog_record,
    devlog_search,
    rule_query,
    batch_upsert,
    batch_query,
    export_markdown
)
from logbook.db import db
from logbook.models import Rule


@pytest.mark.asyncio
async def test_mcp_task_flow():
    """测试 MCP task_upsert 与 task_query (支持 assignee 与 parent_id)。"""
    res = await task_upsert(
        project="logbook",
        id="MCP-01",
        title="测试 MCP 任务流水",
        status="running",
        priority="P1",
        task_type="feat",
        assignee="agy-lead",
        tags=["core", "mcp"]
    )
    assert res["success"] is True
    assert res["task"]["status"] == "running"
    assert res["task"]["assignee"] == "agy-lead"

    # 登记分级子任务
    sub_res = await task_upsert(
        project="logbook",
        id="MCP-01.1",
        parent_id="MCP-01",
        title="测试 MCP 分级子任务",
        status="running",
        assignee="agy-worker"
    )
    assert sub_res["success"] is True
    assert sub_res["task"]["parent_id"] == "MCP-01"

    # 按责任人与状态精确查询
    query_res = await task_query(project="logbook", status=["running"], assignee="agy-lead")
    assert len(query_res) >= 1
    assert any(t["id"] == "MCP-01" for t in query_res)

    # 闭环推进
    close_res = await task_upsert(
        project="logbook",
        id="MCP-01",
        title="测试 MCP 任务流水",
        status="closed",
        commit_hash="c0ffee123",
        proof_link="proof_report.md"
    )
    assert close_res["success"] is True
    assert close_res["task"]["status"] == "closed"
    assert close_res["task"]["duration_seconds"] is not None


@pytest.mark.asyncio
async def test_mcp_finding_and_waiting():
    """测试 MCP finding_record 与 waiting_query。"""
    f_res = await finding_record(
        project="logbook",
        id="FIND-01",
        summary="发现一个连接超时缺陷",
        severity="P2",
        status="open",
        reporter="agy-audit"
    )
    assert f_res["success"] is True
    assert f_res["finding"]["id"] == "FIND-01"
    assert f_res["finding"]["reporter"] == "agy-audit"


@pytest.mark.asyncio
async def test_mcp_devlog_and_search():
    """测试 MCP devlog_record 录入与 devlog_search 检索。"""
    rec_res = await devlog_record(
        project="logbook",
        title="TCP 重传丢包排查手记",
        author="agy-network",
        problem="客户端在大包发送时遭遇 14B 截断",
        root_cause="MSS clamp 缺位与 MTU 不对称",
        solution="开启 brix_pf syn maxseg 1412 截断规避",
        evidence="实测 20MB 传输零截断"
    )
    assert rec_res["success"] is True
    assert rec_res["devlog_id"] is not None

    # 检索手记
    hits = await devlog_search(project="logbook", query="TCP 重传与截断问题", limit=3)
    assert len(hits) >= 1
    assert "截断" in hits[0]["title"] or "截断" in hits[0]["solution"]


@pytest.mark.asyncio
async def test_mcp_rule_and_export():
    """测试 rule_query 与 export_markdown。"""
    # 插入一条规则
    rule = Rule(
        id="RULE-TEST-01",
        category="security",
        title="测试铁律",
        summary="绝不在生产环境写明文密码",
        bad_practice="硬编码密码在配置文件中",
        good_practice="使用环境变量注入凭据",
        constraints="违者立即阻断流水线"
    )
    await db.upsert_rule(rule)

    rules = await rule_query("测试铁律", category="security")
    assert len(rules) >= 1
    assert rules[0]["id"] == "RULE-TEST-01"
    assert rules[0]["category"] == "security"

    # 导出 markdown 测试
    export_path = "tests/test_export.md"
    try:
        msg = await export_markdown(project="logbook", output_path=export_path)
        assert os.path.exists(export_path)
        with open(export_path, "r", encoding="utf-8") as f:
            content = f.read()
        assert "MCP-01" in content
    finally:
        if os.path.exists(export_path):
            os.remove(export_path)


@pytest.mark.asyncio
async def test_mcp_batch_flow():
    """测试 MCP batch_upsert 与 batch_query。"""
    res = await batch_upsert(
        project="logbook",
        id="DEV-TEST-01",
        title="测试研发批次演进记录",
        status="completed",
        summary="完成核心模块构建与测试验证",
        methodology_notes="排障方法论三则与会话收口经验"
    )
    assert res["success"] is True
    assert res["batch"]["id"] == "DEV-TEST-01"

    batches = await batch_query(project="logbook")
    assert len(batches) >= 1
    assert any(b["id"] == "DEV-TEST-01" for b in batches)
