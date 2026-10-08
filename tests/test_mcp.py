"""FastMCP 工具集端到端测试套件 (覆盖 6 大工程优化与自愈能力 + L-A2 错误面/瘦身/投影/brief)"""

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
    export_markdown,
    brief
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
        tags=["core", "mcp"],
        full=True
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
        assignee="agy-worker",
        full=True
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
        proof_link="proof_report.md",
        full=True
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
        commit_hash="abcdef123",
        full=True
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
        notes="实测 6/6 通过，日志零噪音",
        full=True
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
    res = await tasks_bulk_upsert(project="logbook", tasks=tasks_data, batch_id="DEV-BULK-01", full=True)
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
        reporter="agy-audit",
        full=True
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
        methodology_notes="排障方法论三则与会话收口经验",
        full=True
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
        status="open",
        full=True
    )
    assert res["success"] is True
    assert res["waiting"]["id"] == "WAIT-TEST-01"
    assert res["waiting"]["category"] == "user"

    waitings = await waiting_query(project="logbook", status="open")
    assert waitings["success"] is True
    assert waitings["total"] >= 1
    assert any(w["id"] == "WAIT-TEST-01" for w in waitings["items"])


# =============================================================================
# L-A2 增补: 结构化错误面 / 写响应瘦身 / fields 投影 / waiting 多状态 / brief
# =============================================================================

@pytest.mark.asyncio
async def test_mcp_error_surface_structured():
    """P0-2: 全工具体异常统一结构化形态 {isError, error_type, detail}。"""
    # 1. negotiation 裸 ValueError 收编: project 为空 -> INVALID_ARGUMENT 结构化错 (非 SDK 裸包装)
    res = await task_upsert(project="  ", id="X", title="t", status="running")
    assert res["isError"] is True
    assert res["error_type"] == "INVALID_ARGUMENT"
    assert "project" in res["detail"]

    # 2. 越权 PermissionError 收编: logbook 工作区写 brix -> CROSS_PROJECT_FORBIDDEN 结构化错
    res2 = await task_upsert(project="brix", id="X", title="t", status="running")
    assert res2["isError"] is True
    assert res2["error_type"] == "CROSS_PROJECT_FORBIDDEN"
    assert res2["detail"]

    # 3. 不存在项目 -> 协商结构化错 (经外壳统一形态)
    res3 = await task_query(project="no_such_proj_xyz")
    assert res3["isError"] is True
    assert res3["error_type"] == "PROJECT_NEGOTIATION_REQUIRED"


@pytest.mark.asyncio
async def test_mcp_finding_task_precheck():
    """P0-2: finding_record task_id 写前预检 -> TASK_NOT_FOUND 结构化错，且不落库。"""
    before = await finding_query(project="logbook", status=None, limit=200)
    res = await finding_record(
        project="logbook",
        id="FIND-PRECHECK-01",
        summary="悬空关联预检测试",
        status="open",
        task_id="NO-SUCH-TASK-ZZZ",
    )
    assert res["isError"] is True
    assert res["error_type"] == "TASK_NOT_FOUND"
    assert res["task_id"] == "NO-SUCH-TASK-ZZZ"
    after = await finding_query(project="logbook", status=None, limit=200)
    assert before["total"] == after["total"]  # 拒绝写入，无新副本

    # 正向: 关联已存在任务时正常写入瘦身响应
    await task_upsert(project="logbook", id="MCP-PRECHECK-T", title="预检宿主任务", status="running")
    ok = await finding_record(
        project="logbook", id="FIND-PRECHECK-02", summary="合法关联", status="open", task_id="MCP-PRECHECK-T"
    )
    assert ok["success"] is True and ok["ok"] is True and ok["id"] == "FIND-PRECHECK-02"


@pytest.mark.asyncio
async def test_mcp_write_response_slim_default():
    """P1-2: *_record/*_upsert 缺省回 {ok, id, status} 瘦身；full=true 才回全对象。"""
    res = await task_upsert(
        project="logbook", id="MCP-SLIM-01", title="瘦身缺省验证", status="running", priority="P1"
    )
    assert res["success"] is True
    assert res["ok"] is True
    assert res["id"] == "MCP-SLIM-01"
    assert res["status"] == "running"
    assert "task" not in res  # 缺省无全对象

    res_full = await task_upsert(
        project="logbook", id="MCP-SLIM-01", title="瘦身缺省验证", status="running", priority="P1", full=True
    )
    assert res_full["task"]["id"] == "MCP-SLIM-01"
    assert res_full["task"]["priority"] == "P1"

    f = await waiting_record(
        project="logbook", id="WAIT-SLIM-01", description="瘦身验证", category="closing", status="open"
    )
    assert f["ok"] is True and f["id"] == "WAIT-SLIM-01" and f["status"] == "open"
    assert "waiting" not in f

    b = await batch_upsert(project="logbook", id="DEV-SLIM-01", title="瘦身批次", status="completed")
    assert b["ok"] is True and b["id"] == "DEV-SLIM-01" and b["status"] == "completed"
    assert "batch" not in b

    bulk = await tasks_bulk_upsert(
        project="logbook",
        tasks=[{"id": "BULK-SLIM-1", "title": "s1", "status": "planned"}],
    )
    assert bulk["ok"] is True and bulk["total"] == 1 and bulk["ids"] == ["BULK-SLIM-1"]
    assert "items" not in bulk


@pytest.mark.asyncio
async def test_mcp_query_fields_projection_and_clamp():
    """P1-4/P1-5: fields 键投影 + limit 钳制 (1..200)。"""
    await task_upsert(project="logbook", id="MCP-PROJ-01", title="投影验证任务", status="planned", priority="P3")
    q = await task_query(project="logbook", status=["planned"], fields=["id", "status", "title"])
    assert q["success"] is True
    hit = [i for i in q["items"] if i["id"] == "MCP-PROJ-01"]
    assert hit and set(hit[0].keys()) == {"id", "status", "title"}

    # limit 超界钳制: 传入 9999 不炸 (db 层 f-string LIMIT 由 MCP 层钳到 200)
    q2 = await task_query(project="logbook", limit=9999)
    assert q2["success"] is True

    # findings 投影
    fq = await finding_query(project="logbook", status=None, fields=["id", "severity"], limit=5)
    assert all(set(i.keys()) == {"id", "severity"} for i in fq["items"])

    # batches 投影 + 钳制
    bq = await batch_query(project="logbook", limit=0, fields=["id", "title"])
    assert bq["success"] is True  # limit=0 钳为 1, 不产生 SQL 错误


@pytest.mark.asyncio
async def test_mcp_waiting_query_multi_status_and_limit():
    """P1-4: waiting_query 多 status 数组 + limit + 单值向后兼容。"""
    await waiting_record(project="logbook", id="WAIT-MS-OPEN", description="多态开启项", category="user", status="open")
    await waiting_record(
        project="logbook", id="WAIT-MS-CLOSED", description="多态关闭项", category="closing",
        status="closed", resolution="已解决"
    )

    # 单值字符串向后兼容 (既有调用形态)
    open_only = await waiting_query(project="logbook", status="open")
    assert open_only["success"] is True
    ids_open = [w["id"] for w in open_only["items"]]
    assert "WAIT-MS-OPEN" in ids_open and "WAIT-MS-CLOSED" not in ids_open

    # 多状态数组
    both = await waiting_query(project="logbook", status=["open", "closed"])
    ids_both = [w["id"] for w in both["items"]]
    assert "WAIT-MS-OPEN" in ids_both and "WAIT-MS-CLOSED" in ids_both

    # None = 全部
    all_w = await waiting_query(project="logbook", status=None)
    assert all_w["total"] >= both["total"]

    # limit + fields
    lim = await waiting_query(project="logbook", status=["open", "closed"], limit=1, fields=["id"])
    assert lim["total"] <= 1
    assert all(set(w.keys()) == {"id"} for w in lim["items"])


@pytest.mark.asyncio
async def test_mcp_brief_digest():
    """P1-1: logbook_brief digest 端点形态 (A1 未落地时回 BRIEF_UNAVAILABLE 结构化错亦算过)。"""
    res = await brief(project="logbook", devlog_limit=3)
    if res.get("isError"):
        # A1 brief_project 尚未落地: 必须是结构化降级错而非裸异常
        assert res["error_type"] == "BRIEF_UNAVAILABLE"
        return
    assert res["success"] is True and res["ok"] is True
    assert res["project"] == "logbook"
    for key in ("tasks_open", "findings_open", "waitings_open", "batch_running", "recent_devlogs"):
        assert key in res["brief"]
    assert isinstance(res["text"], str) and res["text"].startswith("# [logbook] 开工简报")


@pytest.mark.asyncio
async def test_mcp_export_markdown_returns_content():
    """P2-4: export_markdown 缺省返回内容字符串不落盘; 显式 output_path 保留兼容落盘。"""
    content = await export_markdown(project="logbook")
    assert isinstance(content, str) and "MCP-01" in content  # 内容字符串直返

    # 向后兼容: 显式路径仍服务端落盘
    export_path = "tests/test_export_compat.md"
    try:
        msg = await export_markdown(project="logbook", output_path=export_path)
        assert os.path.exists(export_path)
        assert "导出" in msg
    finally:
        if os.path.exists(export_path):
            os.remove(export_path)
