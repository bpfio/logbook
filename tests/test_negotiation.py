"""Logbook 智能协商与 MCP 2.x 全维能力测试套件

测试项:
1. 拼写错误自愈协商 (briz -> brix)
2. 越权跨项目物理阻断
3. MCP 结构化协商响应 (isError=True + suggestions)
4. MCP 2.x Resources (只读上下文) 直读
5. MCP 2.x Prompts (规程模板) 渲染
"""

import pytest
import os
from logbook.negotiation import (
    validate_and_negotiate_project,
    ProjectNegotiationError,
    detect_current_workspace_project
)
from logbook.mcp_server import (
    task_upsert,
    get_active_tasks_resource,
    prompt_start_batch,
    prompt_record_devlog
)
from logbook.db import db


@pytest.mark.asyncio
async def test_negotiation_typo_correction():
    """测试将 briz 智能模糊匹配为 brix 的协商建议能力。"""
    await db.ensure_project("brix")

    # 1. 直接调用协商校验器
    with pytest.raises(ProjectNegotiationError) as exc_info:
        await validate_and_negotiate_project("briz")

    err = exc_info.value
    assert err.requested_project == "briz"
    assert "brix" in err.suggestions
    assert "相近合法项目建议" in err.message


@pytest.mark.asyncio
async def test_mcp_tool_negotiation_response():
    """测试 Agent 调用 MCP 工具时输错项目名获得结构化自愈指引。"""
    # 模拟 Agent 发送错误指令 project='briz'
    resp = await task_upsert(
        project="briz",
        id="F99",
        title="测试打错项目名",
        status="running"
    )

    # 断言：返回结构化协商错误，而不是崩溃
    assert resp["isError"] is True
    assert resp["error_type"] == "PROJECT_NEGOTIATION_REQUIRED"
    assert resp["requested_project"] == "briz"
    assert "brix" in resp["suggestions"]

    # 模拟 Agent 接收建议后自省修正，重新调用 project='brix' (需匹配当前工作区或无工作区冲突)
    # 本测试目录为 logbook，若要写入 brix，需保证物理工作区放行或无冲突
    curr_ws = detect_current_workspace_project()
    if curr_ws == "logbook":
        # 在 logbook 目录下尝试写入 logbook 正确项目
        correct_resp = await task_upsert(
            project="logbook",
            id="L99",
            title="测试自愈写入",
            status="running"
        )
        assert correct_resp["success"] is True
        assert correct_resp["project"] == "logbook"


@pytest.mark.asyncio
async def test_cross_project_permission_boundary():
    """测试跨项目越权物理拦截 (身处 logbook 仓库妄图篡改 brix)。"""
    curr_ws = detect_current_workspace_project()
    assert curr_ws == "logbook"  # 当前物理工作区正是 logbook

    # 试图指定 brix
    with pytest.raises(PermissionError, match="越权阻断"):
        await validate_and_negotiate_project("brix")


@pytest.mark.asyncio
async def test_mcp_resources_and_prompts():
    """测试 MCP 2.x Resources 直读与 Prompts 模板生成。"""
    # 1. 只读资源测试
    res_text = await get_active_tasks_resource("logbook")
    assert isinstance(res_text, str)
    assert "活跃任务" in res_text or "当前无活跃" in res_text

    # 2. Prompts 模板测试
    p_batch = prompt_start_batch("logbook", "DEV-2026-10-07-03")
    assert "AGENTS.md" in p_batch
    assert "DEV-2026-10-07-03" in p_batch

    p_devlog = prompt_record_devlog("logbook", "L02")
    assert "故障现象 (Problem)" in p_devlog
    assert "根因定位 (Root Cause)" in p_devlog
