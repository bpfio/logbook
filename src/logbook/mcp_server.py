"""Logbook FastMCP Server (全维 MCP 2.x 规范支持)

特性:
- 强制要求显式 project 参数 (无隐式推导)
- 集成智能交互协商与模糊纠错 (Did-You-Mean)
- Tools (动作执行面) + Resources (只读上下文面) + Prompts (规程模板面)
- 0 开放公网端口，原生走系统 Stdio 管道
"""

import sys
import os
import asyncio
from typing import Any, Literal
try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer

from .models import (
    Task, TaskStatus, TaskPriority, TaskType,
    Finding, FindingStatus, FindingSeverity,
    Waiting, WaitingStatus, WaitingCategory,
    Batch, BatchStatus,
    DevLog, DevLogVisibility, Rule
)
from .sanitizer import sanitize_text
from .time_sync import get_beijing_now, format_beijing
from .vector import get_embedding
from .converter import export_devlog_markdown
from .negotiation import validate_and_negotiate_project, ProjectNegotiationError
from .db import db

# 创建 MCP Server 实例
mcp = MCPServer("logbook-mcp-server")


# =============================================================================
# 一、 Resources (只读上下文面 - 零开销挂载)
# =============================================================================

@mcp.resource("logbook://{project}/tasks/active")
async def get_active_tasks_resource(project: str) -> str:
    """提供指定项目当前所有活跃 (running) 与阻塞 (blocked) 任务的只读 Markdown 快照。"""
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
        tasks = await db.query_tasks(proj, status=[TaskStatus.RUNNING, TaskStatus.BLOCKED])
        if not tasks:
            return f"项目 [{proj}] 当前无活跃或阻塞中的任务。"
        lines = [f"# 项目 [{proj}] 活跃任务看板", "", "| ID | 状态 | 优先级 | 标题 | 开工时间 |", "|---|---|---|---|---|"]
        for t in tasks:
            lines.append(f"| {t.id} | {t.status.value} | {t.priority.value} | {t.title} | {format_beijing(t.started_at)} |")
        return "\n".join(lines)
    except Exception as e:
        return f"读取活跃任务失败: {e}"


@mcp.resource("logbook://{project}/waitings/open")
async def get_open_waitings_resource(project: str) -> str:
    """提供指定项目所有未决待办与待用户裁决项的只读快照。"""
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
        waitings = await db.query_waitings(proj, status=WaitingStatus.OPEN)
        if not waitings:
            return f"项目 [{proj}] 当前无阻塞待办项。"
        lines = [f"# 项目 [{proj}] 未决待办与裁决项", "", "| ID | 类别 | 事项描述 | 阻塞时刻 |", "|---|---|---|---|"]
        for w in waitings:
            lines.append(f"| {w.id} | {w.category.value} | {w.description} | {format_beijing(w.blocked_at)} |")
        return "\n".join(lines)
    except Exception as e:
        return f"读取阻塞待办失败: {e}"


@mcp.resource("logbook://rules/engineering")
async def get_engineering_rules_resource() -> str:
    """提供跨项目全局统一的架构铁律与工程红线 (SSOT)。"""
    try:
        rules = await db.query_rules()
        if not rules:
            return "当前暂无架构铁律记录。"
        lines = ["# 团队统一工程红线与架构铁律 (SSOT)", ""]
        for r in rules:
            lines.extend([
                f"## [{r.id}] {r.title}",
                f"- **核心原则**: {r.summary}",
                f"- **错误示范**: {r.bad_practice}",
                f"- **正确做法**: {r.good_practice}",
                f"- **边界约束**: {r.constraints or '-'}",
                ""
            ])
        return "\n".join(lines)
    except Exception as e:
        return f"读取架构铁律失败: {e}"


@mcp.resource("logbook://schema/fields")
async def get_schema_fields_resource() -> str:
    """提供 Logbook 数据字典、实体规范与字段约束清单 (面向 Agent 字段指引)。"""
    return """# Logbook 工业级研发台账数据字典与字段正典 (SSOT)

## 1. 任务台账 (tasks)
- id: 任务编号 (必填, 如 'F14', 'L05.1')
- title: 任务标题 (必填, 256字符内)
- status: 状态机 (必填, 枚举: 'planned', 'running', 'blocked', 'closed', 'wontfix')
- task_type: 工程类型 (枚举: 'feat', 'fix', 'verify', 'investigation', 'drill', 'docs', 'ops', 'deploy')
- priority: 优先级 (枚举: 'P0', 'P1', 'P2', 'P3')
- assignee: 协同锁责任人 (如 'agy', 'agent_xxx')
- parent_id: 父任务编号 (支持树状层级解耦)
- commit_hash: 交付提交散列 (closed 状态必填/强推荐)
- proof_link: 报告或证据指针 (如 '报告 dfc50ef')
- batch_id: 归属研发批次号 (如 'DEV-2026-10-07-02')

## 2. 研发批次 (batches)
- id: 批次编号 (必填, 如 'DEV-2026-10-07-02')
- title: 批次标题 (必填)
- status: 批次状态 (枚举: 'planned', 'running', 'completed', 'halted')
- summary: 任务源、目标与方案概述
- methodology_notes: 排障方法论、未办结复作入口与讨论过程

## 3. 根因排查手记 (devlogs)
- title: 故障或排查简述 (必填)
- problem: 【故障现象】具体错误日志、复现路径与环境 (必填)
- root_cause: 【根因定位】机理分析，穿透至代码行或系统内核 (必填)
- solution: 【解决方案】明确修复逻辑与架构重构 (必填)
- evidence: 【验证证据】复现与修复后的实测比对输出 (必填)
- author: 记录者 (默认 'agy')
- visibility: 密级 ('project_private', 'public_safe')

## 4. 发现台账 (findings)
- id: 发现编号 (如 'F-320', 'AUDIT-P1-1')
- source: 来源 (如 'audit', 'realmachine', 'F-322池')
- severity: 严重级别 ('P1', 'P2', 'P3')
- status: 处置状态 ('open', 'infix', 'fixed', 'wontfix', 'blocked')
- resolution: 处置结果或修复任务编号

## 5. 待办与阻塞 (waitings)
- id: 待办编号 (如 'WAIT-F320', 'CLOSE-1')
- description: 阻塞事项详细描述与唤醒条件 (必填)
- category: 类别 ('user'=待人类裁决, 'closing'=收尾动作, 'external'=外部依赖)
- status: 状态 ('open', 'closed')
- owner: 责任人 (默认 'user')
"""


# =============================================================================
# 二、 Prompts (规程模板面 - 一键正典驱动)
# =============================================================================

@mcp.prompt("start_batch")
def prompt_start_batch(project: str, batch_id: str) -> str:
    """研发开工规程：自动注入任务台账与正典纪律约束。"""
    return f"""你现在正在执行项目 [{project}] 的研发批次 [{batch_id}]。
请严格执行 AGENTS.md 研发纪律：
1. 【行前必读】：开工前必须先查询当前所有未结任务与最新状态；
2. 【状态推进】：开工时必须调用 task_upsert 将目标任务标记为 running；
3. 【证据闭环】：办结 (closed) 时必须提交真实的 commit_hash 或实测报告指针，严禁虚假闭环；
4. 【红线铁律】：方案先行，执行前必须经用户显式批准，严禁先斩后奏。"""


@mcp.prompt("record_devlog")
def prompt_record_devlog(project: str, task_id: str) -> str:
    """排查手记沉淀规程：引导 Agent 严格按故障四要素输出复盘。"""
    return f"""请针对项目 [{project}] 的任务 [{task_id}] 沉淀深度根因复盘手记。
手记必须严格覆盖结构化四要素：
- 【故障现象 (Problem)】：具体错误日志、复现路径与环境参数
- 【根因定位 (Root Cause)】：机理分析，穿透至代码行或系统内核
- 【解决方案 (Solution)】：明确修复逻辑与架构重构
- 【验证证据 (Evidence)】：复现与修复后的实测比对输出
请整理完成后调用 devlog_record 工具入库。"""


# =============================================================================
# 三、 Tools (动作执行面 - 强制显式传参 + 智能协商自愈)
# =============================================================================

@mcp.tool()
async def task_upsert(
    project: str,
    id: str,
    title: str,
    status: Literal["planned", "running", "blocked", "closed", "wontfix"],
    task_type: Literal["feat", "fix", "verify", "investigation", "drill", "docs", "ops", "deploy"] = "fix",
    priority: Literal["P0", "P1", "P2", "P3"] = "P2",
    assignee: str | None = "agy",
    parent_id: str | None = None,
    commit_hash: str | None = None,
    proof_link: str | None = None,
    notes: str | None = None,
    tags: list[str] | None = None,
    batch_id: str | None = None,
) -> dict:
    """原子登记或推进任务状态机。强制要求显式提供 project 参数。支持 assignee 责任归属与 parent_id 分级解耦。

    字段说明与规程要求:
    - project: 必填。项目代号 (如 'brix', 'logbook')。
    - id: 必填。任务唯一标识 (如 'F14', 'L05.1')。
    - title: 必填。任务目标与简述 (256字符内)。
    - status: 必填。阶段状态机:
        * 'planned': 已规划待办
        * 'running': 进行中 (开工必标记)
        * 'blocked': 阻塞中 (等待用户裁决或外部依赖)
        * 'closed': 办结闭环 (必须带 commit_hash 或 proof_link 真实证据)
        * 'wontfix': 经评估不予修复并备案
    - task_type: 工程类型 ('feat', 'fix', 'verify', 'investigation', 'drill', 'docs', 'ops', 'deploy')。
    - priority: 优先级 ('P0'=阻塞故障, 'P1'=核心严重, 'P2'=中度演练, 'P3'=轻微文档)。
    - assignee: 责任人锁 (防止多 Agent 争抢，如 'agy', 'agent_xxx')。
    - parent_id: 父任务 ID (支持树状拆解)。
    - commit_hash: 关联提交散列。
    - proof_link: 报告或证据指针 (如 '报告 dfc50ef')。
    - batch_id: 归属研发批次号 (如 'DEV-2026-10-07-02')。
    """
    try:
        proj = await validate_and_negotiate_project(project)
    except ProjectNegotiationError as e:
        return e.to_dict()

    s_title = sanitize_text(title).clean_text
    s_notes = sanitize_text(notes).clean_text if notes else None

    task = Task(
        id=id,
        title=s_title,
        task_type=TaskType(task_type),
        priority=TaskPriority(priority),
        status=TaskStatus(status),
        assignee=assignee,
        parent_id=parent_id,
        commit_hash=commit_hash,
        proof_link=proof_link,
        notes=s_notes,
        tags=tags or [],
        batch_id=batch_id,
    )
    saved = await db.upsert_task(proj, task)
    return {
        "success": True,
        "project": proj,
        "task": saved.model_dump(mode="json")
    }


@mcp.tool()
async def task_query(
    project: str,
    status: list[Literal["planned", "running", "blocked", "closed", "wontfix"]] | None = None,
    priority: list[Literal["P0", "P1", "P2", "P3"]] | None = None,
    batch_id: str | None = None,
    assignee: str | None = None,
    parent_id: str | None = None,
    limit: int = 50,
) -> list[dict] | dict:
    """多维查询任务看板。强制要求显式提供 project 参数。支持按状态、优先级、批次、责任人与父任务过滤。"""
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
    except ProjectNegotiationError as e:
        return e.to_dict()

    st_enums = [TaskStatus(s) for s in status] if status else None
    pr_enums = [TaskPriority(p) for p in priority] if priority else None
    tasks = await db.query_tasks(
        proj,
        status=st_enums,
        priority=pr_enums,
        batch_id=batch_id,
        assignee=assignee,
        parent_id=parent_id,
        limit=limit
    )
    return [t.model_dump(mode="json") for t in tasks]


@mcp.tool()
async def finding_record(
    project: str,
    id: str,
    summary: str,
    source: str = "audit",
    severity: Literal["P1", "P2", "P3"] = "P2",
    status: Literal["open", "infix", "fixed", "wontfix", "blocked"] = "open",
    task_id: str | None = None,
    reporter: str = "audit",
    resolution: str | None = None,
) -> dict:
    """登记或更新缺陷/审计发现项。强制要求显式提供 project 参数。支持 reporter 责任归属。

    字段说明:
    - project: 必填。项目代号。
    - id: 必填。发现项 ID (如 'F-320', 'AUDIT-P1-1')。
    - summary: 必填。缺陷现象描述与备注。
    - source: 发现来源 (如 'audit', 'realmachine', 'F-322池', 'drill-F15')。
    - severity: 严重级别 ('P1', 'P2', 'P3')。
    - status: 处置状态 ('open', 'infix', 'fixed', 'wontfix', 'blocked')。
    - task_id: 关联修复任务 ID。
    - reporter: 报告者 Agent。
    - resolution: 处置结果或修复 Commit 标识。
    """
    try:
        proj = await validate_and_negotiate_project(project)
    except ProjectNegotiationError as e:
        return e.to_dict()

    s_summary = sanitize_text(summary).clean_text
    s_resolution = sanitize_text(resolution).clean_text if resolution else None

    finding = Finding(
        id=id,
        source=source,
        severity=FindingSeverity(severity),
        status=FindingStatus(status),
        task_id=task_id,
        reporter=reporter,
        summary=s_summary,
        resolution=s_resolution,
    )
    saved = await db.upsert_finding(proj, finding)
    return {"success": True, "project": proj, "finding": saved.model_dump(mode="json")}


@mcp.tool()
async def waiting_query(
    project: str,
    status: Literal["open", "closed"] = "open"
) -> list[dict] | dict:
    """查询待办与阻塞项 (如待用户决策事项)。强制要求显式提供 project 参数。"""
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
    except ProjectNegotiationError as e:
        return e.to_dict()

    st = WaitingStatus(status)
    waitings = await db.query_waitings(proj, status=st)
    return [w.model_dump(mode="json") for w in waitings]


@mcp.tool()
async def waiting_record(
    project: str,
    id: str,
    description: str,
    category: Literal["user", "closing", "external"] = "user",
    status: Literal["open", "closed"] = "open",
    owner: str = "user",
    resolution: str | None = None,
) -> dict:
    """登记或更新待办与阻塞项。强制要求显式提供 project 参数。

    字段说明:
    - project: 必填。项目代号。
    - id: 必填。阻塞待办 ID (如 'WAIT-F320', 'CLOSE-1')。
    - description: 必填。阻塞事项描述与唤醒条件。
    - category: 类别 ('user'=需用户决策/授权, 'closing'=批次收尾动作, 'external'=外部依赖)。
    - status: 状态 ('open'=阻塞中, 'closed'=已解决)。
    - owner: 责任人 (默认 'user')。
    - resolution: 解决结论。
    """
    try:
        proj = await validate_and_negotiate_project(project)
    except ProjectNegotiationError as e:
        return e.to_dict()

    waiting = Waiting(
        id=id,
        category=WaitingCategory(category),
        owner=owner,
        status=WaitingStatus(status),
        description=sanitize_text(description).clean_text,
        resolution=sanitize_text(resolution).clean_text if resolution else None,
    )
    saved = await db.upsert_waiting(proj, waiting)
    return {"success": True, "project": proj, "waiting": saved.model_dump(mode="json")}


@mcp.tool()
async def devlog_record(
    project: str,
    title: str,
    problem: str,
    root_cause: str,
    solution: str,
    evidence: str,
    author: str = "agy",
    task_id: str | None = None,
    visibility: Literal["project_private", "public_safe"] = "project_private",
    tags: list[str] | None = None,
) -> dict:
    """结构化录入排查手记 (强制覆盖根因四要素 + 自动脱敏 + 512维向量入库)。强制要求显式提供 project 参数。支持 author 溯源。

    结构化四要素必填项:
    - project: 必填。项目代号。
    - title: 必填。排查主题或故障简述。
    - problem: 必填。【故障现象】具体错误日志、复现路径与环境参数。
    - root_cause: 必填。【根因定位】机理分析，穿透至代码行或系统内核。
    - solution: 必填。【解决方案】明确修复逻辑与架构重构。
    - evidence: 必填。【验证证据】复现与修复后的实测比对输出。
    - author: 记录者 Agent 或人类专家 (默认 'agy')。
    - visibility: 密级 ('project_private'=项目私有, 'public_safe'=全局开源脱敏)。
    """
    try:
        proj = await validate_and_negotiate_project(project)
    except ProjectNegotiationError as e:
        return e.to_dict()

    # 强制脱敏
    s_title = sanitize_text(title).clean_text
    s_problem = sanitize_text(problem).clean_text
    s_rc = sanitize_text(root_cause).clean_text
    s_sol = sanitize_text(solution).clean_text
    s_ev = sanitize_text(evidence).clean_text

    # 计算 512 维向量
    full_text = f"{s_title} {s_problem} {s_rc} {s_sol}"
    embed_res = await get_embedding(full_text)

    devlog = DevLog(
        project_id=proj,
        task_id=task_id,
        title=s_title,
        author=author,
        problem=s_problem,
        root_cause=s_rc,
        solution=s_sol,
        evidence=s_ev,
        visibility=DevLogVisibility(visibility),
        tags=tags or [],
        embedding=embed_res.embedding,
    )
    saved = await db.record_devlog(devlog)
    return {
        "success": True,
        "devlog_id": saved.id,
        "project": proj,
        "vector_source": embed_res.source,
        "message": f"排查手记已成功入库 [{proj}] 并生成向量索引"
    }


@mcp.tool()
async def devlog_search(
    project: str,
    query: str,
    limit: int = 5,
) -> list[dict] | dict:
    """语义向量与全文混合检索跨 Agent 长期记忆。优先当前项目私密经验 + 全局开源安全经验。"""
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
    except ProjectNegotiationError as e:
        return e.to_dict()

    s_query = sanitize_text(query).clean_text
    embed_res = await get_embedding(s_query)
    
    results = await db.search_devlogs(
        project=proj,
        query_vector=embed_res.embedding,
        query_text=s_query if not embed_res.embedding else None,
        limit=limit
    )
    return results


@mcp.tool()
async def rule_query(
    keyword: str | None = None,
    category: Literal["general", "network", "kernel", "database", "security", "engineering", "governance", "resource"] | None = None
) -> list[dict]:
    """开工前对齐架构铁律与工程红线 (SSOT)。全项目共享。支持按领域 category 精准过滤。"""
    rules = await db.query_rules(keyword=keyword, category=category)
    return [r.model_dump(mode="json") for r in rules]


@mcp.tool()
async def export_markdown(project: str, output_path: str = "docs/DEVLOG.md") -> str:
    """从数据库生成完全兼容 brix 格式的 DEVLOG.md。强制要求显式提供 project 参数。"""
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
    except ProjectNegotiationError as e:
        return e.message

    md = await export_devlog_markdown(proj)
    from pathlib import Path
    out_p = Path(output_path)
    out_p.parent.mkdir(parents=True, exist_ok=True)
    out_p.write_text(md, encoding="utf-8")
    return f"已成功将项目 {proj} 的最新状态导出至 {output_path} (行数: {len(md.splitlines())})"


@mcp.tool()
async def batch_upsert(
    project: str,
    id: str,
    title: str,
    status: Literal["planned", "running", "completed", "halted"] = "completed",
    branch_name: str | None = None,
    summary: str | None = None,
    methodology_notes: str | None = None,
) -> dict:
    """登记或更新研发批次演进记录与排障方法论。强制要求显式提供 project 参数。

    字段说明:
    - project: 必填。项目代号。
    - id: 必填。批次编号 (如 'DEV-2026-10-07-02')。
    - title: 必填。批次标题与主题。
    - status: 批次状态 ('planned', 'running', 'completed', 'halted')。
    - branch_name: 关联代码分支。
    - summary: 批次任务源与方案总结。
    - methodology_notes: 排障方法论、未办结复作入口与讨论过程。
    """
    try:
        proj = await validate_and_negotiate_project(project)
    except ProjectNegotiationError as e:
        return e.to_dict()

    batch = Batch(
        id=id,
        title=title,
        status=BatchStatus(status),
        branch_name=branch_name,
        summary=summary,
        methodology_notes=methodology_notes,
    )
    saved = await db.upsert_batch(proj, batch)
    return {"success": True, "project": proj, "batch": saved.model_dump(mode="json")}


@mcp.tool()
async def batch_query(project: str, limit: int = 20) -> list[dict] | dict:
    """查询项目历史研发批次演进记录与讨论复盘。强制要求显式提供 project 参数。"""
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
    except ProjectNegotiationError as e:
        return e.to_dict()

    batches = await db.query_batches(proj, limit=limit)
    return [b.model_dump(mode="json") for b in batches]


def run_stdio():
    """以 Stdio 模式运行 MCP Server。"""
    mcp.run()


if __name__ == "__main__":
    run_stdio()
