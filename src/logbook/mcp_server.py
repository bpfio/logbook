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
import functools
import asyncpg
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
from .errors import tool_error, ToolError, pg_tool_error
from .normalizer import (
    normalize_status, normalize_task_type, normalize_priority,
    normalize_severity, normalize_waiting_category
)
from .db import db
# P1-1: brief digest 端点 (A1 并行开发中, 接口契约已锁 db.brief_project(project_id, devlog_limit=5) -> dict,
# 键 tasks_open/findings_open/waitings_open/batch_running/recent_devlogs; A1 未落地期间取不到属预期)
brief_project = getattr(db, "brief_project", None)
from typing import Annotated
from pydantic import BeforeValidator

TaskStatusArg = Annotated[TaskStatus, BeforeValidator(normalize_status)]
TaskTypeArg = Annotated[TaskType, BeforeValidator(normalize_task_type)]
TaskPriorityArg = Annotated[TaskPriority, BeforeValidator(normalize_priority)]
FindingSeverityArg = Annotated[FindingSeverity, BeforeValidator(normalize_severity)]
WaitingCategoryArg = Annotated[WaitingCategory, BeforeValidator(normalize_waiting_category)]

# 创建 MCP Server 实例
mcp = MCPServer("logbook-mcp-server")


# =============================================================================
# 零、统一结构化错误外壳 (P0-2) + 通用工具函数
# =============================================================================

QUERY_LIMIT_MAX = 200


def tool_shell(fn):
    """P0-2 结构化错误面: 工具体任何异常统一转 {isError, error_type, detail, ...}。

    - ProjectNegotiationError / ToolError (含 negotiation 收编的 ValueError/PermissionError): 原样结构化
    - asyncpg.PostgresError: 映射约束名与诊断 detail
    - 其余异常: INTERNAL_ERROR 兜底 (保留类型名与消息，杜绝 SDK 零细节裸包装)
    """
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        try:
            return await fn(*args, **kwargs)
        except asyncio.CancelledError:
            raise
        except ProjectNegotiationError as e:
            return e.to_dict()
        except ToolError as e:
            return e.to_dict()
        except asyncpg.PostgresError as e:
            return pg_tool_error(e, tool=fn.__name__)
        except Exception as e:
            return tool_error("INTERNAL_ERROR", f"[{fn.__name__}] {type(e).__name__}: {e}", tool=fn.__name__)
    return wrapper


def clamp_limit(limit: int, maximum: int = QUERY_LIMIT_MAX) -> int:
    """P1-4: LIMIT 参数化钳制 (下限 1, 上限 200)。"""
    try:
        n = int(limit)
    except (TypeError, ValueError):
        return 50
    return max(1, min(n, maximum))


def project_fields(items: list[dict], fields: list[str] | None) -> list[dict]:
    """P1-5: 查询结果 MCP 层键投影 (fields=None 返回全列)。"""
    if not fields:
        return items
    keys = [f.strip() for f in fields if isinstance(f, str) and f.strip()]
    return [{k: it[k] for k in keys if k in it} for it in items]


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


@mcp.resource("logbook://projects")
async def get_projects_resource() -> str:
    """提供所有已注册项目的清单与 Git 仓库坐标。"""
    try:
        projs = await db.list_projects()
        if not projs:
            return "当前暂无已注册项目。"
        lines = ["# Logbook 已注册项目中心", "", "| Git 仓库坐标 | 物理 Schema | 项目全称 |", "|---|---|---|"]
        for p in projs:
            lines.append(f"| {p.slug} | {p.schema_name} | {p.title} |")
        return "\n".join(lines)
    except Exception as e:
        return f"读取项目列表失败: {e}"


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
@tool_shell
async def project_init(
    repo: str,
    title: str,
    description: str | None = None,
    schema_name: str | None = None,
) -> dict:
    """初始化并开立新项目的 Logbook 研发台账空间 (DDL Schema 幂等创建)。

    字段说明:
    - repo: 必填。权威 Git 仓库标识 (如 'bpfio/brix', 'bpfio/logbook', 'io/TS') 或项目短名。
    - title: 必填。项目全称或中文名称 (如 '出口退税涉税风险穿透分析与研判')。
    - description: 可选。项目背景与核心职责简述。
    - schema_name: 可选。指定物理 Schema 名称 (缺省由 repo 尾部名称自动安全派生)。
    """
    clean_repo = sanitize_text(repo).clean_text.strip()
    clean_title = sanitize_text(title).clean_text.strip()
    clean_desc = sanitize_text(description).clean_text.strip() if description else None

    if not clean_repo or not clean_title:
        return tool_error("INVALID_ARGUMENT", "repo 与 title 参数均为必填项，禁止为空！", tool="project_init")

    proj = await db.register_project(
        slug=clean_repo,
        title=clean_title,
        description=clean_desc,
        schema_name=schema_name
    )

    return {
        "success": True,
        "ok": True,
        "project": proj.schema_name,
        "repo": proj.slug,
        "title": proj.title,
        "message": f"项目 [{proj.slug}] 研发台账空间已成功开通！物理 Schema: [{proj.schema_name}]"
    }


@mcp.tool()
@tool_shell
async def task_upsert(
    project: str,
    id: str,
    title: str,
    status: TaskStatusArg,
    task_type: TaskTypeArg = TaskType.FIX,
    priority: TaskPriorityArg = TaskPriority.P2,
    assignee: str | None = "agy",
    parent_id: str | None = None,
    commit_hash: str | None = None,
    proof_link: str | None = None,
    notes: str | None = None,
    tags: list[str] | None = None,
    batch_id: str | None = None,
    allow_cross_project: bool = False,
    full: bool = False,
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
    - allow_cross_project: 显式放行跨项目写入授权 (默认 False)。
    - full: 可选。True 时回显任务全对象 (缺省 False 仅回瘦身 {ok, id, status})。
    """
    try:
        proj = await validate_and_negotiate_project(project, allow_cross_project=allow_cross_project)
    except ProjectNegotiationError as e:
        return e.to_dict()

    s_title = sanitize_text(title).clean_text
    s_notes = sanitize_text(notes).clean_text if notes else None

    # BeforeValidator 已在前置校验阶段将参数自动规范化为 Enum 实例
    st_val = status.value if isinstance(status, TaskStatus) else normalize_status(status)
    tt_val = task_type.value if isinstance(task_type, TaskType) else normalize_task_type(task_type)
    pr_val = priority.value if isinstance(priority, TaskPriority) else normalize_priority(priority)

    # 针对非代码演练任务，若无 commit_hash 且提供了实测 notes，则自动锚定 notes 为 proof_link 满足证据闭环断言
    if st_val == "closed" and not commit_hash and not proof_link and s_notes:
        proof_link = f"notes: {s_notes[:100]}"

    task = Task(
        id=id,
        title=s_title,
        task_type=TaskType(tt_val),
        priority=TaskPriority(pr_val),
        status=TaskStatus(st_val),
        assignee=assignee,
        parent_id=parent_id,
        commit_hash=commit_hash,
        proof_link=proof_link,
        notes=s_notes,
        tags=tags or [],
        batch_id=batch_id,
    )
    saved = await db.upsert_task(proj, task)
    slim = {"success": True, "ok": True, "project": proj, "id": id, "status": st_val}
    if full:
        slim["task"] = saved.model_dump(mode="json")
    return slim


@mcp.tool()
@tool_shell
async def tasks_bulk_upsert(
    project: str,
    tasks: list[dict],
    batch_id: str | None = None,
    allow_cross_project: bool = False,
    full: bool = False,
) -> dict:
    """原子批量登记或更新任务台账 (单事务批量落库，大幅压减网络 RTT 往返开销)。

    参数说明:
    - project: 必填。项目代号。
    - tasks: 必填。任务字典列表，单项包含 id, title, status 等。
    - batch_id: 可选。统一归属研发批次号 (若任务项本身未指定，则以此默认填充)。
    - allow_cross_project: 显式放行跨项目写入授权 (默认 False)。
    - full: 可选。True 时回显 items 全对象列表 (缺省 False 仅回瘦身 {ok, total, ids})。
    """
    try:
        proj = await validate_and_negotiate_project(project, allow_cross_project=allow_cross_project)
    except ProjectNegotiationError as e:
        return e.to_dict()

    # 若指定了统一批次号，自动保底确保 batches 存在以满足外键约束
    if batch_id:
        await db.upsert_batch(proj, Batch(id=batch_id, title=f"研发批次 {batch_id}", status=BatchStatus.RUNNING))

    task_objs = []
    for t in tasks:
        tid = t.get("id")
        if not tid:
            continue
        title = sanitize_text(t.get("title", "")).clean_text
        norm_st = normalize_status(t.get("status", "planned"))
        norm_tt = normalize_task_type(t.get("task_type", "fix"))
        norm_pr = normalize_priority(t.get("priority", "P2"))
        notes = sanitize_text(t.get("notes", "")).clean_text if t.get("notes") else None
        b_id = t.get("batch_id") or batch_id
        c_hash = t.get("commit_hash")
        p_link = t.get("proof_link")
        if norm_st == "closed" and not c_hash and not p_link and notes:
            p_link = f"notes: {notes[:100]}"

        task_objs.append(Task(
            id=tid,
            title=title,
            task_type=TaskType(norm_tt),
            priority=TaskPriority(norm_pr),
            status=TaskStatus(norm_st),
            assignee=t.get("assignee", "agy"),
            parent_id=t.get("parent_id"),
            commit_hash=c_hash,
            proof_link=p_link,
            notes=notes,
            tags=t.get("tags") or [],
            batch_id=b_id,
        ))

    saved_tasks = await db.bulk_upsert_tasks(proj, task_objs)
    slim = {
        "success": True,
        "ok": True,
        "project": proj,
        "total": len(saved_tasks),
        "ids": [t.id for t in saved_tasks],
    }
    if full:
        slim["items"] = [t.model_dump(mode="json") for t in saved_tasks]
    return slim


@mcp.tool()
@tool_shell
async def task_query(
    project: str,
    status: list[Literal["planned", "running", "blocked", "closed", "wontfix"]] | None = None,
    priority: list[Literal["P0", "P1", "P2", "P3"]] | None = None,
    batch_id: str | None = None,
    assignee: str | None = None,
    parent_id: str | None = None,
    limit: int = 50,
    fields: list[str] | None = None,
) -> dict:
    """多维查询任务看板。强制要求显式提供 project 参数。返回标准化包装对象。

    - limit: 返回条数上限 (1..200, 缺省 50, 超界自动钳制)。
    - fields: 可选列投影 (如 ['id','status','priority','title'])，缺省回全列。
    """
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
    except ProjectNegotiationError as e:
        return e.to_dict()

    limit = clamp_limit(limit)
    st_enums = [TaskStatus(normalize_status(s)) for s in status] if status else None
    pr_enums = [TaskPriority(normalize_priority(p)) for p in priority] if priority else None
    tasks = await db.query_tasks(
        proj,
        status=st_enums,
        priority=pr_enums,
        batch_id=batch_id,
        assignee=assignee,
        parent_id=parent_id,
        limit=limit
    )
    items = project_fields([t.model_dump(mode="json") for t in tasks], fields)
    return {
        "success": True,
        "project": proj,
        "total": len(items),
        "items": items
    }


@mcp.tool()
@tool_shell
async def finding_record(
    project: str,
    id: str,
    summary: str,
    source: str = "audit",
    severity: FindingSeverityArg = FindingSeverity.P2,
    status: Literal["open", "infix", "fixed", "wontfix", "blocked"] = "open",
    task_id: str | None = None,
    reporter: str = "audit",
    resolution: str | None = None,
    allow_cross_project: bool = False,
    full: bool = False,
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
    - allow_cross_project: 显式放行跨项目写入授权 (默认 False)。
    - task_id 写前预检: 关联任务不存在时回 TASK_NOT_FOUND 结构化错。
    - full: 可选。True 时回显发现项全对象 (缺省 False 仅回瘦身 {ok, id, status})。
    """
    try:
        proj = await validate_and_negotiate_project(project, allow_cross_project=allow_cross_project)
    except ProjectNegotiationError as e:
        return e.to_dict()

    # P0-2: task_id 写前预检 (今晚 F-243 实锤形态: 静默写入悬空外键)
    if task_id:
        async with db._pool.acquire() as conn:
            hit = await conn.fetchrow(f'SELECT 1 FROM "{proj}".tasks WHERE id = $1', task_id)
        if not hit:
            return tool_error(
                "TASK_NOT_FOUND",
                f"关联任务 [{task_id}] 在项目 [{proj}] 的任务台账中不存在，拒绝写入悬空关联。"
                f"请先 task_upsert 登记该任务或修正 task_id。",
                project=proj, task_id=task_id, finding_id=id,
            )

    s_summary = sanitize_text(summary).clean_text
    s_resolution = sanitize_text(resolution).clean_text if resolution else None

    sev_val = severity.value if isinstance(severity, FindingSeverity) else normalize_severity(severity)

    finding = Finding(
        id=id,
        source=source,
        severity=FindingSeverity(sev_val),
        status=FindingStatus(status),
        task_id=task_id,
        reporter=reporter,
        summary=s_summary,
        resolution=s_resolution,
    )
    saved = await db.upsert_finding(proj, finding)
    slim = {"success": True, "ok": True, "project": proj, "id": id, "status": status}
    if full:
        slim["finding"] = saved.model_dump(mode="json")
    return slim


@mcp.tool()
@tool_shell
async def finding_query(
    project: str,
    status: list[Literal["open", "infix", "fixed", "wontfix", "blocked"]] | None = None,
    severity: list[Literal["P1", "P2", "P3"]] | None = None,
    limit: int = 50,
    fields: list[str] | None = None,
) -> dict:
    """查询缺陷与审计发现项。强制要求显式提供 project 参数。返回标准化包装对象。

    - limit: 返回条数上限 (1..200, 缺省 50, 超界自动钳制)。
    - fields: 可选列投影 (如 ['id','severity','status','summary'])，缺省回全列。
    """
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
    except ProjectNegotiationError as e:
        return e.to_dict()

    limit = clamp_limit(limit)
    st_enums = [FindingStatus(s) for s in status] if status else None
    sev_enums = [FindingSeverity(normalize_severity(s)) for s in severity] if severity else None
    findings = await db.query_findings(proj, status=st_enums, severity=sev_enums, limit=limit)
    items = project_fields([f.model_dump(mode="json") for f in findings], fields)
    return {
        "success": True,
        "project": proj,
        "total": len(items),
        "items": items
    }


@mcp.tool()
@tool_shell
async def waiting_query(
    project: str,
    status: list[Literal["open", "closed"]] | Literal["open", "closed"] | None = "open",
    limit: int = 50,
    fields: list[str] | None = None,
) -> dict:
    """查询待办与阻塞项 (如待用户决策事项)。强制要求显式提供 project 参数。返回标准化包装对象。

    - status: 支持 'open'/'closed' 单值或多状态数组 (如 ['open','closed'])，None 查全部。
    - limit: 返回条数上限 (1..200, 缺省 50, 超界自动钳制)。
    - fields: 可选列投影 (如 ['id','category','description'])，缺省回全列。
    """
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
    except ProjectNegotiationError as e:
        return e.to_dict()

    limit = clamp_limit(limit)
    if status is None:
        st_list: list[WaitingStatus] | None = None
    elif isinstance(status, str):
        st_list = [WaitingStatus(status)]
    else:
        st_list = [WaitingStatus(s) for s in status]

    if st_list is None or len(st_list) == 1:
        rows = await db.query_waitings(proj, status=st_list[0] if st_list else None)
    else:
        # P1-4 多状态: db 层仅支持单 status，MCP 层全量拉取后过滤 (不依赖 db 改动)
        rows = [w for w in await db.query_waitings(proj) if w.status in st_list]

    rows = sorted(rows, key=lambda w: w.blocked_at or 0, reverse=True)[:limit]
    items = project_fields([w.model_dump(mode="json") for w in rows], fields)
    return {
        "success": True,
        "project": proj,
        "total": len(items),
        "items": items
    }


@mcp.tool()
@tool_shell
async def waiting_record(
    project: str,
    id: str,
    description: str,
    category: WaitingCategoryArg = WaitingCategory.USER,
    status: Literal["open", "closed"] = "open",
    owner: str = "user",
    resolution: str | None = None,
    allow_cross_project: bool = False,
    full: bool = False,
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
    - allow_cross_project: 显式放行跨项目写入授权 (默认 False)。
    - full: 可选。True 时回显待办全对象 (缺省 False 仅回瘦身 {ok, id, status})。
    """
    try:
        proj = await validate_and_negotiate_project(project, allow_cross_project=allow_cross_project)
    except ProjectNegotiationError as e:
        return e.to_dict()

    cat_val = category.value if isinstance(category, WaitingCategory) else normalize_waiting_category(category)

    waiting = Waiting(
        id=id,
        category=WaitingCategory(cat_val),
        owner=owner,
        status=WaitingStatus(status),
        description=sanitize_text(description).clean_text,
        resolution=sanitize_text(resolution).clean_text if resolution else None,
    )
    saved = await db.upsert_waiting(proj, waiting)
    slim = {"success": True, "ok": True, "project": proj, "id": id, "status": status}
    if full:
        slim["waiting"] = saved.model_dump(mode="json")
    return slim


@mcp.tool()
@tool_shell
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
    id: int | None = None,
    allow_cross_project: bool = False,
) -> dict:
    """结构化录入排查手记 (支持幂等去重与向量缓存 + 强制覆盖根因四要素 + 自动脱敏)。强制要求显式提供 project 参数。支持 author 溯源。

    结构化四要素必填项:
    - project: 必填。项目代号。
    - title: 必填。排查主题或故障简述。
    - problem: 必填。【故障现象】具体错误日志、复现路径与环境参数。
    - root_cause: 必填。【根因定位】机理分析，穿透至代码行或系统内核。
    - solution: 必填。【解决方案】明确修复逻辑与架构重构。
    - evidence: 必填。【验证证据】复现与修复后的实测比对输出。
    - author: 记录者 Agent 或人类专家 (默认 'agy')。
    - task_id: 关联任务编号。
    - visibility: 密级 ('project_private'=项目私有, 'public_safe'=全局开源脱敏)。
    - id: 可选。指定排查手记 ID 执行幂等更新。
    - allow_cross_project: 显式放行跨项目写入授权 (默认 False)。
    """
    try:
        proj = await validate_and_negotiate_project(project, allow_cross_project=allow_cross_project)
    except ProjectNegotiationError as e:
        return e.to_dict()

    # 强制脱敏
    s_title = sanitize_text(title).clean_text
    s_problem = sanitize_text(problem).clean_text
    s_rc = sanitize_text(root_cause).clean_text
    s_sol = sanitize_text(solution).clean_text
    s_ev = sanitize_text(evidence).clean_text

    # 1. 检查是否存在同任务/同主题手记 (幂等查重与向量缓存复用)
    existing = await db.find_existing_devlog(proj, devlog_id=id, task_id=task_id, title=s_title)
    if existing:
        content_unchanged = (
            existing.get("title") == s_title and
            existing.get("problem") == s_problem and
            existing.get("root_cause") == s_rc and
            existing.get("solution") == s_sol and
            existing.get("evidence") == s_ev
        )
        if content_unchanged:
            # 文本未变，仅更新元数据，复用已有向量
            updated = await db.update_devlog(
                devlog_id=existing["id"],
                title=s_title,
                author=author,
                problem=s_problem,
                root_cause=s_rc,
                solution=s_sol,
                evidence=s_ev,
                visibility=visibility,
                tags=tags or [],
                embedding=None,
                task_id=task_id,
            )
            return {
                "success": True,
                "ok": True,
                "devlog_id": updated["id"],
                "project": proj,
                "vector_source": "cached_skip",
                "message": f"排查手记 [{updated['id']}] 内容未变化，已更新元数据并复用已有向量缓存"
            }
        else:
            # 文本发生变更，重新计算 512 维向量
            full_text = f"{s_title} {s_problem} {s_rc} {s_sol}"
            embed_res = await get_embedding(full_text)
            updated = await db.update_devlog(
                devlog_id=existing["id"],
                title=s_title,
                author=author,
                problem=s_problem,
                root_cause=s_rc,
                solution=s_sol,
                evidence=s_ev,
                visibility=visibility,
                tags=tags or [],
                embedding=embed_res.embedding,
                task_id=task_id,
            )
            return {
                "success": True,
                "ok": True,
                "devlog_id": updated["id"],
                "project": proj,
                "vector_source": embed_res.source,
                "message": f"排查手记 [{updated['id']}] 内容已更新并重新生成向量索引"
            }

    # 2. 全新手记：计算 512 维向量并录入
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
        "ok": True,
        "devlog_id": saved.id,
        "project": proj,
        "vector_source": embed_res.source,
        "message": f"排查手记已成功入库 [{proj}] 并生成向量索引"
    }


@mcp.tool()
@tool_shell
async def devlog_search(
    project: str,
    query: str,
    limit: int = 5,
    fields: list[str] | None = None,
) -> dict:
    """语义向量与全文混合检索跨 Agent 长期记忆。优先当前项目私密经验 + 全局开源安全经验。返回标准化包装对象。

    - limit: 返回条数上限 (1..200, 缺省 5, 超界自动钳制)。
    - fields: 可选列投影 (如 ['id','title','root_cause'])，缺省回全列。
    """
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
    except ProjectNegotiationError as e:
        return e.to_dict()

    limit = clamp_limit(limit, maximum=QUERY_LIMIT_MAX)
    s_query = sanitize_text(query).clean_text
    embed_res = await get_embedding(s_query)

    results = await db.search_devlogs(
        project=proj,
        query_vector=embed_res.embedding,
        query_text=s_query if not embed_res.embedding else None,
        limit=limit
    )
    items = project_fields([dict(r) for r in results], fields)
    return {
        "success": True,
        "project": proj,
        "total": len(items),
        "items": items
    }


@mcp.tool()
@tool_shell
async def rule_query(
    keyword: str | None = None,
    category: Literal["general", "network", "kernel", "database", "security", "engineering", "governance", "resource"] | None = None,
    fields: list[str] | None = None
) -> dict:
    """开工前对齐架构铁律与工程红线 (SSOT)。全项目共享。支持按领域 category 精准过滤。返回标准化包装对象。

    - fields: 可选列投影 (如 ['id','title','summary'])，缺省回全列。
    """
    rules = await db.query_rules(keyword=keyword, category=category)
    items = project_fields([r.model_dump(mode="json") for r in rules], fields)
    return {
        "success": True,
        "total": len(items),
        "items": items
    }


@mcp.tool()
@tool_shell
async def export_markdown(project: str, output_path: str | None = None) -> str:
    """从数据库生成完全兼容 brix 格式的 DEVLOG.md，以内容字符串返回 (调用方自行落盘)。

    P2-4: 服务端缺省不再写任意服务器路径。output_path 参数保留向后兼容:
    显式传入时服务端仍代写该文件并返回摘要 (旧客户端零破坏)。
    强制要求显式提供 project 参数。
    """
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
    except ProjectNegotiationError as e:
        return e.message

    md = await export_devlog_markdown(proj)
    if output_path:
        # 向后兼容: 旧客户端显式指定路径时保留服务端落盘行为
        from pathlib import Path
        out_p = Path(output_path)
        out_p.parent.mkdir(parents=True, exist_ok=True)
        out_p.write_text(md, encoding="utf-8")
        return f"已成功将项目 {proj} 的最新状态导出至 {output_path} (行数: {len(md.splitlines())})"
    return md


@mcp.tool()
@tool_shell
async def batch_upsert(
    project: str,
    id: str,
    title: str,
    status: Literal["planned", "running", "completed", "halted"] = "completed",
    branch_name: str | None = None,
    summary: str | None = None,
    methodology_notes: str | None = None,
    allow_cross_project: bool = False,
    full: bool = False,
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
    - allow_cross_project: 显式放行跨项目写入授权 (默认 False)。
    - full: 可选。True 时回显批次全对象 (缺省 False 仅回瘦身 {ok, id, status})。
    """
    try:
        proj = await validate_and_negotiate_project(project, allow_cross_project=allow_cross_project)
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
    slim = {"success": True, "ok": True, "project": proj, "id": id, "status": status}
    if full:
        slim["batch"] = saved.model_dump(mode="json")
    return slim


@mcp.tool()
@tool_shell
async def batch_query(project: str, limit: int = 20, fields: list[str] | None = None) -> dict:
    """查询项目历史研发批次演进记录与讨论复盘。强制要求显式提供 project 参数。返回标准化包装对象。

    - limit: 返回条数上限 (1..200, 缺省 20, 超界自动钳制)。
    - fields: 可选列投影 (如 ['id','title','status'])，缺省回全列。
    """
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
    except ProjectNegotiationError as e:
        return e.to_dict()

    limit = clamp_limit(limit)
    batches = await db.query_batches(proj, limit=limit)
    items = project_fields([b.model_dump(mode="json") for b in batches], fields)
    return {
        "success": True,
        "project": proj,
        "total": len(items),
        "items": items
    }


@mcp.tool(name="logbook_brief")
@tool_shell
async def brief(project: str, devlog_limit: int = 5) -> dict:
    """一次调用返回项目开工简报 digest：open tasks/findings/waitings + running 批次 + 最近 N 条 devlog 标题。单包目标 ≤2K token。

    - project: 必填。项目代号。
    - devlog_limit: 最近手记条数 (缺省 5)。
    - text 字段为 token 友好的紧凑行文本，优先消费；brief 字段为结构化原始数据。
    """
    proj = await validate_and_negotiate_project(project, is_write=False)

    if brief_project is None:
        return tool_error(
            "BRIEF_UNAVAILABLE",
            "db 层 brief_project 尚未落地 (A1 并行开发中)，请暂用 task_query/finding_query/waiting_query/batch_query/devlog_search 组合。",
        )

    data = await brief_project(proj, devlog_limit=clamp_limit(devlog_limit, maximum=QUERY_LIMIT_MAX))

    t_open = data.get("tasks_open") or []
    f_open = data.get("findings_open") or []
    w_open = data.get("waitings_open") or []
    b_run = data.get("batch_running")
    devlogs = data.get("recent_devlogs") or []

    # token 友好格式: 紧凑行文本优先 (batch_running 兼容 dict / list[dict] / str)
    if isinstance(b_run, dict):
        b_list = [b_run]
    elif isinstance(b_run, list):
        b_list = [b for b in b_run if isinstance(b, dict)]
    else:
        b_list = []
    b_head = b_list[0]["id"] if b_list else "-"

    lines = [f"# [{proj}] 开工简报 | tasks_open={len(t_open)} findings_open={len(f_open)} waitings_open={len(w_open)} batch_running={b_head}"]
    for t in t_open:
        lines.append(f"T: {t.get('id')} {t.get('status')} {t.get('priority')} {t.get('title')}")
    for f in f_open:
        lines.append(f"F: {f.get('id')} {f.get('severity')} {f.get('status')} {(f.get('summary') or '')[:80]}")
    for w in w_open:
        lines.append(f"W: {w.get('id')} {w.get('category')} {(w.get('description') or '')[:80]}")
    for b in b_list:
        lines.append(f"B: {b.get('id')} {b.get('title')}")
    for d in devlogs:
        if isinstance(d, dict):
            lines.append(f"D: [{d.get('id')}] {d.get('title')}")

    return {
        "success": True,
        "ok": True,
        "project": proj,
        "text": "\n".join(lines),
        "brief": data,
    }


def run_stdio():
    """以 Stdio 模式运行 MCP Server。"""
    mcp.run()


if __name__ == "__main__":
    run_stdio()
