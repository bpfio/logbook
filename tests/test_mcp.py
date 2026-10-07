"""FastMCP 工具集端到端测试套件 (覆盖 6 大工程优化与自愈能力)"""

import pytest
import os
from logbook.mcp_server import (
    task_upsert,
    tasks_bulk_upsert,
    task_query,
    finding_record,
    finding_query,
    waiting_query,
    waiting_record,
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

    # 按责任人与状态精确查询 (验证统一包装字典)
    query_res = await task_query(project="logbook", status=["running"], assignee="agy-lead")
    assert query_res["success"] is True
    assert query_res["total"] >= 1
    assert any(t["id"] == "MCP-01" for t in query_res["items"])

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
async def test_mcp_normalizer_and_non_code_evidence():
    """测试状态/类型入参归一化防呆与非代码演练任务实测证据闭环。"""
    # 1. 传入携带 Emoji 和同义词的参数: '✅ closed', 'bugfix', 'high'
    res = await task_upsert(
        project="logbook",
        id="MCP-NORM-01",
        title="测试归一化防呆",
        status="✅ closed",
        task_type="bugfix",
        priority="high",
        commit_hash="abcdef123"
    )
    assert res["success"] is True
    assert res["task"]["status"] == "closed"
    assert res["task"]["task_type"] == "fix"
    assert res["task"]["priority"] == "P1"

    # 2. 非代码演练任务 (drill) 仅提供 notes 实测报告闭环，不填 commit_hash
    res_drill = await task_upsert(
        project="logbook",
        id="MCP-DRILL-01",
        title="测试演练任务免 commit 闭环",
        status="done",
        task_type="drill",
        notes="实测 6/6 通过，日志零噪音"
    )
    assert res_drill["success"] is True
    assert res_drill["task"]["status"] == "closed"
    assert res_drill["task"]["task_type"] == "drill"


@pytest.mark.asyncio
async def test_mcp_tasks_bulk_upsert():
    """测试 tasks_bulk_upsert 单事务原子批量写入。"""
    tasks_data = [
        {"id": "BULK-01", "title": "批量任务1", "status": "planned", "task_type": "feat"},
        {"id": "BULK-02", "title": "批量任务2", "status": "running", "task_type": "fix"},
        {"id": "BULK-03", "title": "批量任务3", "status": "closed", "task_type": "docs", "notes": "文档就绪"},
    ]
    res = await tasks_bulk_upsert(project="logbook", tasks=tasks_data, batch_id="DEV-BULK-01")
    assert res["success"] is True
    assert res["total"] == 3
    assert all(t["batch_id"] == "DEV-BULK-01" for t in res["items"])

    # 查询验证
    q = await task_query(project="logbook", batch_id="DEV-BULK-01")
    assert q["total"] == 3


@pytest.mark.asyncio
async def test_mcp_finding_and_waiting():
    """测试 MCP finding_record, finding_query 与 waiting_query。"""
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

    # 查询发现项
    fq = await finding_query(project="logbook", status=["open"])
    assert fq["success"] is True
    assert fq["total"] >= 1
    assert any(f["id"] == "FIND-01" for f in fq["items"])


@pytest.mark.asyncio
async def test_mcp_devlog_and_search_with_cache():
    """测试 MCP devlog_record 录入、幂等去重与缓存向量复用。"""
    # 首次录入
    rec1 = await devlog_record(
        project="logbook",
        task_id="MCP-LOG-01",
        title="TCP 重传丢包排查手记",
        author="agy-network",
        problem="客户端在大包发送时遭遇 14B 截断",
        root_cause="MSS clamp 缺位与 MTU 不对称",
        solution="开启 brix_pf syn maxseg 1412 截断规避",
        evidence="实测 20MB 传输零截断"
    )
    assert rec1["success"] is True
    assert rec1["devlog_id"] is not None
    dev_id = rec1["devlog_id"]

    # 再次重复录入相同内容：应命中缓存，跳过外部 API
    rec2 = await devlog_record(
        project="logbook",
        task_id="MCP-LOG-01",
        title="TCP 重传丢包排查手记",
        author="agy-network-v2",
        problem="客户端在大包发送时遭遇 14B 截断",
        root_cause="MSS clamp 缺位与 MTU 不对称",
        solution="开启 brix_pf syn maxseg 1412 截断规避",
        evidence="实测 20MB 传输零截断"
    )
    assert rec2["success"] is True
    assert rec2["devlog_id"] == dev_id
    assert rec2["vector_source"] == "cached_skip"

    # 检索手记 (验证统一包装字典)
    hits = await devlog_search(project="logbook", query="TCP 重传与截断问题", limit=3)
    assert hits["success"] is True
    assert hits["total"] >= 1
    assert "截断" in hits["items"][0]["title"] or "截断" in hits["items"][0]["solution"]


@pytest.mark.asyncio
async def test_mcp_rule_and_export():
    """测试 rule_query 与 export_markdown。"""
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
    assert rules["success"] is True
    assert rules["total"] >= 1
    assert rules["items"][0]["id"] == "RULE-TEST-01"

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
    assert batches["success"] is True
    assert batches["total"] >= 1
    assert any(b["id"] == "DEV-TEST-01" for b in batches["items"])


@pytest.mark.asyncio
async def test_mcp_waiting_flow():
    """测试 MCP waiting_record 与 waiting_query。"""
    res = await waiting_record(
        project="logbook",
        id="WAIT-TEST-01",
        description="等待用户裁决方案选型",
        category="user",
        status="open"
    )
    assert res["success"] is True
    assert res["waiting"]["id"] == "WAIT-TEST-01"
    assert res["waiting"]["category"] == "user"

    waitings = await waiting_query(project="logbook", status="open")
    assert waitings["success"] is True
    assert waitings["total"] >= 1
    assert any(w["id"] == "WAIT-TEST-01" for w in waitings["items"])
