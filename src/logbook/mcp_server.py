"""Logbook FastMCP Server (全维 MCP 2.x 规范支持)

特性:
- 强制要求显式 project 参数 (无隐式推导)
- 集成智能交互协商与模糊纠错 (Did-You-Mean)
- Tools (动作执行面) + Resources (只读上下文面) + Prompts (规程模板面)
- 0 开放公网端口，原生走系统 Stdio 管道
"""

import sys
import os
import re
import socket
import asyncio
import functools
import asyncpg
from datetime import datetime, date
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
    DevLog, DevLogVisibility, Rule,
    AgentMessage, FileLease,
    Research, ResearchCategory, ResearchStatus,
    Dataset, DataRecord, DatasetCategory, RecordStatus,
    slug_to_schema_name,
)
from .sanitizer import sanitize_text
from .time_sync import get_beijing_now, format_beijing
from .vector import get_embedding
from .converter import export_devlog_markdown
from .negotiation import validate_and_negotiate_project, ProjectNegotiationError
from .errors import tool_error, ToolError, pg_tool_error
from .normalizer import (
    normalize_status, normalize_task_type, normalize_priority,
    normalize_severity, normalize_waiting_category,
    normalize_research_category, normalize_research_status,
    normalize_dataset_category, normalize_record_status
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
ResearchCategoryArg = Annotated[ResearchCategory, BeforeValidator(normalize_research_category)]
ResearchStatusArg = Annotated[ResearchStatus, BeforeValidator(normalize_research_status)]

# 创建 MCP Server 实例 (version 注入用于生产部署构建辨识)
try:
    from importlib.metadata import version as _pkg_version
    _SERVER_VERSION = _pkg_version("logbook")
except Exception:
    _SERVER_VERSION = "0.4.2"

mcp = MCPServer("logbook-mcp-server", version=_SERVER_VERSION)


# =============================================================================
# 零、统一结构化错误外壳 (P0-2) + 通用工具函数
# =============================================================================

QUERY_LIMIT_MAX = 200


def _trusted_agent_identity(explicit_agent: str | None = None) -> tuple[str | None, dict | None]:
    trusted_identity = os.getenv("LOGBOOK_AGENT_ID", "").strip()
    if not trusted_identity:
        return None, tool_error(
            "CALLER_IDENTITY_REQUIRED",
            "缺少可信 LOGBOOK_AGENT_ID，无法执行多 Agent 身份操作",
        )
    if explicit_agent is not None and explicit_agent.strip() != trusted_identity:
        return None, tool_error(
            "CALLER_IDENTITY_MISMATCH",
            "调用参数中的 Agent 身份与可信身份不一致",
        )
    return trusted_identity, None


async def _require_admin(action: str) -> str | None:
    """群组管理面 admin 门禁。

    - 有可信身份: 必须为 shared.principals.is_admin, 否则 ADMIN_REQUIRED 拒绝;
    - 无身份 (本机 legacy/CLI 模式): 视为系统面全权, 保持存量行为。
    """
    identity = os.getenv("LOGBOOK_AGENT_ID", "").strip()
    if not identity:
        return None
    if not await db.is_admin(identity):
        raise ToolError(
            "ADMIN_REQUIRED",
            f"[{action}] 属于群组管理面操作, 仅 admin 账户可执行; 当前身份 [{identity}] 非 admin。",
        )
    return identity


async def _session_bootstrap():
    """stdio/SSH 会话启动引导 (幂等): ACL 基座 + 账户注册与 PG 角色同步。

    任一失败均降级 legacy 不降权模式 (与存量单用户行为一致), 不阻断服务启动。
    """
    identity = os.getenv("LOGBOOK_AGENT_ID", "").strip()
    if not identity:
        return
    try:
        await db.ensure_acl_bootstrap()
        await db.ensure_principal(identity)
    except Exception as e:
        print(f"[logbook] ACL bootstrap degraded to legacy mode: {type(e).__name__}: {e}", file=sys.stderr)
    finally:
        # 引导用连接不跨事件循环携带, 服务循环内按需重建
        try:
            await db.close()
        except Exception:
            pass


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


def detect_node_ip() -> str:
    """自动探测当前节点 IP 地址 (优先 socket.gethostbyname，故障回退 127.0.0.1)。"""
    try:
        ip = socket.gethostbyname(socket.gethostname())
        if ip and ip != "127.0.0.1":
            return ip
    except Exception:
        pass
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except Exception:
        pass
    return "127.0.0.1"


def detect_caller_ip(explicit_ip: str | None = None) -> str:
    """自动探测调用端 Agent IP:
    1. 优先使用显式入参 explicit_ip；
    2. 其次从 SSH 会话环境变量 SSH_CLIENT / SSH_CONNECTION 获取远端客户端实际 IP；
    3. 再次通过 socket/UDP 探测本地宿主 IP；
    4. 兜底回退 127.0.0.1。
    """
    if explicit_ip and explicit_ip.strip():
        return explicit_ip.strip()

    ssh_client = os.environ.get("SSH_CLIENT") or os.environ.get("SSH_CONNECTION")
    if ssh_client:
        parts = ssh_client.strip().split()
        if parts and parts[0]:
            return parts[0]

    return detect_node_ip()


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


@mcp.resource("logbook://{project}/leases/active")
async def get_active_leases_resource(project: str) -> str:
    """提供指定项目当前所有有效且未过期的代码文件租约软锁看板。"""
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
        leases = await db.query_file_leases(proj)
        if not leases:
            return f"项目 [{proj}] 当前无活跃文件租约 (全部代码文件可自由申请)。"
        lines = [
            f"# 项目 [{proj}] 活跃文件租约看板",
            "",
            "| 文件路径 | 锁定 Agent | 来源 IP | 租约到期时间 |",
            "|---|---|---|---|"
        ]
        for l in leases:
            lines.append(f"| {l.file_path} | {l.agent_name} | {l.agent_ip} | {format_beijing(l.lease_expires_at)} |")
        return "\n".join(lines)
    except Exception as e:
        return f"读取活跃文件租约失败: {e}"


@mcp.resource("logbook://{project}/researches/recent")
async def get_recent_researches_resource(project: str) -> str:
    """提供指定项目最近沉淀的技术调研、选型决策与对抗代价评估快照。"""
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
        researches = await db.query_researches(proj, limit=10)
        if not researches:
            return f"项目 [{proj}] 当前暂无技术调研与架构选型记录。"
        lines = [f"# 项目 [{proj}] 架构调研与技术决策快照 (最近 10 项)", ""]
        for r in researches:
            cat_val = r.category.value if hasattr(r.category, "value") else str(r.category)
            status_val = r.status.value if hasattr(r.status, "value") else str(r.status)
            lines.extend([
                f"## [{cat_val}] {r.title} ({status_val})",
                f"- **调研目的**: {r.objective}",
                f"- **业界现状**: {r.market_landscape or '-'}",
                f"- **对抗成本与权衡**: {r.tradeoffs or '-'}",
                f"- **选型决策**: {r.decision or '-'}",
                f"- **权威参考**: {r.references or '-'}",
                f"- **作者/节点**: {r.author} ({r.agent_ip or '未知 IP'}) | 归档时间: {format_beijing(r.created_at)}",
                ""
            ])
        return "\n".join(lines)
    except Exception as e:
        return f"读取技术调研记录失败: {e}"


@mcp.resource("logbook://{project}/mailbox/summary")
async def get_mailbox_summary_resource(project: str) -> str:
    """提供指定项目内对讲信箱的未读待办看板与信件编号索引。"""
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
        summaries = await db.get_mailbox_summary(proj)
        if not summaries:
            return f"项目 [{proj}] 当前信箱全部已读 (无未决待收信件)。"
        lines = [
            f"# 项目 [{proj}] 对讲信箱未读看板",
            "",
            "| 收件 Agent | 未读信件数 | 待提取信件 ID 清单 | 最近投递时间 |",
            "|---|---|---|---|"
        ]
        for s in summaries:
            ids_str = ", ".join(f"#{i}" for i in s.get("unread_ids", []))
            lines.append(f"| {s['to_agent']} | {s['unread_count']} | {ids_str} | {format_beijing(s['latest_at'])} |")
        lines.extend([
            "",
            "> 提示：目标 Agent 可直接调用 `message_read(message_id=...)` 精确提取信件详情并自动核销。"
        ])
        return "\n".join(lines)
    except Exception as e:
        return f"读取信箱摘要失败: {e}"


@mcp.resource("logbook://mailbox/{agent_name}/inbox")
async def get_agent_personal_inbox_resource(agent_name: str) -> str:
    """提供指定 Agent 的全局个人信箱未读与近期消息 Markdown 视图 (零开销直读)。"""
    try:
        name = agent_name.strip()
        trusted_identity, identity_error = _trusted_agent_identity(name)
        if identity_error:
            return f"拒绝读取 Agent [{name}] 的个人信箱: {identity_error['error_type']}"
        msgs = await db.get_agent_inbox(project_id=None, agent_name=name, unread_only=True, limit=20)
        is_unread = True
        if not msgs:
            is_unread = False
            msgs = await db.get_agent_inbox(project_id=None, agent_name=name, unread_only=False, limit=10)
        if not msgs:
            return f"Agent [{name}] 的个人信箱为空 (无历史或未读信件)。"
        status_desc = "未读信件" if is_unread else "近期历史信件 (最近 10 封)"
        lines = [
            f"# Agent [{name}] 个人信箱 ({status_desc})",
            "",
            "| ID | 项目 | 发件 Agent | 发件端真实 IP | 主题 | 状态 | 投递时间 |",
            "|---|---|---|---|---|---|---|"
        ]
        for m in msgs:
            st = "未读" if not m.is_read else "已读"
            lines.append(f"| {m.id} | {m.project_id} | {m.from_agent} | {m.from_ip} | {m.subject} | {st} | {format_beijing(m.created_at)} |")
        lines.extend([
            "",
            "> 提示：阅读正文详情请调用工具 `message_read(message_id=...)` 或发送回信 `message_send(...)`。"
        ])
        return "\n".join(lines)
    except Exception as e:
        return f"读取 Agent [{agent_name}] 个人信箱失败: {e}"


@mcp.resource("logbook://{project}/mailbox/{agent_name}")
async def get_agent_project_mailbox_resource(project: str, agent_name: str) -> str:
    """提供指定项目内某 Agent 的专属信箱未读与近期消息 Markdown 视图。"""
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
        name = agent_name.strip()
        trusted_identity, identity_error = _trusted_agent_identity(name)
        if identity_error:
            return f"拒绝读取项目 [{project}] Agent [{name}] 的信箱: {identity_error['error_type']}"
        msgs = await db.get_agent_inbox(project_id=proj, agent_name=name, unread_only=True, limit=20)
        is_unread = True
        if not msgs:
            is_unread = False
            msgs = await db.get_agent_inbox(project_id=proj, agent_name=name, unread_only=False, limit=10)
        if not msgs:
            return f"项目 [{proj}] 中 Agent [{name}] 的专属信箱为空。"
        status_desc = "未读信件" if is_unread else "近期历史信件 (最近 10 封)"
        lines = [
            f"# 项目 [{proj}] Agent [{name}] 专属信箱 ({status_desc})",
            "",
            "| ID | 发件 Agent | 发件端真实 IP | 主题 | 状态 | 投递时间 |",
            "|---|---|---|---|---|---|"
        ]
        for m in msgs:
            st = "未读" if not m.is_read else "已读"
            lines.append(f"| {m.id} | {m.from_agent} | {m.from_ip} | {m.subject} | {st} | {format_beijing(m.created_at)} |")
        lines.extend([
            "",
            "> 提示：阅读正文详情请调用工具 `message_read(message_id=...)` 或发送回信 `message_send(...)`。"
        ])
        return "\n".join(lines)
    except Exception as e:
        return f"读取项目 [{project}] Agent [{agent_name}] 信箱失败: {e}"


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


@mcp.prompt("conduct_research")
def prompt_conduct_research(project: str, topic: str) -> str:
    """技术调研五要素驱动规程：开工前禁止重复造轮子与对抗成本评估引导。"""
    return f"""你现在正在针对项目 [{project}] 开展主题为 [{topic}] 的技术调研。
请严格遵循全局架构与调研铁律：
1. 【调研先行，禁止重复造轮子】：先查阅业界/社区成熟开源方案与标准库，评估是否有可直接复用或借鉴的成熟资产；
2. 【评估对抗成本】：技术选型不仅看优势，必须深入评估我们要对抗该组件的什么默认行为 (如线程模型、内存开销、上下文切换等)，列出对抗代价；
3. 【0/1 与 1-100 证据纪律】：有良率/指标数据即证明产线或能力存在 (0/1 跨越)，质量优劣是量变指标 (1-100 提升)，两者不得混淆；
4. 【沉淀五要素】：输出必须包含 目标与背景 (objective)、市场现状与选型清单 (market_landscape)、优劣权衡与对抗成本 (tradeoffs)、最终选型决策 (decision)、权威参考 (references)；
5. 【入库归档】：调研梳理完毕后，必须调用 research_record 工具持久化入库，作为全团队知识底座。"""


@mcp.prompt("agent_collaborate")
def prompt_agent_collaborate(project: str, target_file: str, partner_agent: str) -> str:
    """跨 Agent 协同作业规程：引导排他软锁申请与对讲信件联动。"""
    return f"""你现在正准备在项目 [{project}] 中修改核心代码文件 [{target_file}]，并与伙伴 [{partner_agent}] 协同作业。
请严格执行多 Agent 协同正典：
1. 【排他锁抢占】：在修改前必须先调用 lease_acquire(project='{project}', file_path='{target_file}') 申请独占租约；
2. 【租约冲突规避】：若抢占失败 (返回锁定中)，必须等待或联系持有者释放，严禁强行覆盖写代码；
3. 【对讲通知】：抢占成功后，必须调用 message_send 向 [{partner_agent}] 发送对讲信件，说明修改目的与预计耗时；
4. 【用后即释】：代码修改与测试验收通过后，必须调用 lease_release 及时释放租约。"""


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
    groups: list[str] | None = None,
) -> dict:
    """初始化并开立新项目的 Logbook 研发台账空间 (DDL Schema 幂等创建)。

    字段说明:
    - repo: 必填。权威 Git 仓库标识 (如 'bpfio/brix', 'bpfio/logbook', 'io/TS') 或项目短名。
    - title: 必填。项目全称或中文名称 (如 '出口退税涉税风险穿透分析与研判')。
    - description: 可选。项目背景与核心职责简述。
    - schema_name: 可选。指定物理 Schema 名称 (缺省由 repo 尾部名称自动安全派生)。
    - groups: 可选。建库时挂载的群组 (挂载即收口: 组内互见互写, 跨组不可见);
      缺省挂载调用者所在的全部群组; admin 可指定任意群组。
      不挂组 = legacy 开放态 (全体账户可见可写), 可后续由 admin group_grant 收口。
    """
    identity = os.getenv("LOGBOOK_AGENT_ID", "").strip() or None
    clean_repo = sanitize_text(repo).clean_text.strip()
    clean_title = sanitize_text(title).clean_text.strip()
    clean_desc = sanitize_text(description).clean_text.strip() if description else None

    if not clean_repo or not clean_title:
        return tool_error("INVALID_ARGUMENT", "repo 与 title 参数均为必填项，禁止为空！", tool="project_init")

    # 多用户语义守卫: 项目物理存在但当前身份不可见 → 语义化拒绝 (不泄露存在性细节以外的信息)
    probe_schema = schema_name or slug_to_schema_name(clean_repo)
    probe = await db.probe_project(probe_schema, agent=identity)
    if probe["exists"] and not probe["visible"] and not (identity and await db.is_admin(identity)):
        return tool_error(
            "PROJECT_NOT_VISIBLE",
            f"项目 [{probe_schema}] 已存在且已挂载群组, 您的账户无访问权 (跨组不可见); 如需接入请联系 admin 将您加入对应群组。",
            project=probe_schema,
            tool="project_init",
        )

    proj = await db.register_project(
        slug=clean_repo,
        title=clean_title,
        description=clean_desc,
        schema_name=schema_name
    )

    # 建库挂组: 新建项目挂请求群组 (或调用者所在群组); 已存在项目不自动改挂载面 (由 admin 管理)
    was_new = not probe["exists"]
    attached: list[str] = []
    if was_new and db.acl_bootstrapped:
        if groups:
            if identity and not await db.is_admin(identity):
                mine = set(await db.get_principal_groups(identity))
                disallowed = set(groups) - mine
                if disallowed:
                    return tool_error(
                        "ADMIN_REQUIRED",
                        f"非 admin 账户仅能将新项目挂载到自己所在的群组; 无权挂载: {sorted(disallowed)}",
                        tool="project_init",
                    )
            for g in sorted(set(groups)):
                try:
                    await db.grant_group_project(g, proj.schema_name, operator=identity)
                    attached.append(g)
                except ValueError:
                    return tool_error("GROUP_NOT_FOUND", f"群组 [{g}] 不存在, 请先 group_record 创建。", tool="project_init")
        elif identity:
            for g in await db.get_principal_groups(identity):
                try:
                    await db.grant_group_project(g, proj.schema_name, operator=identity)
                    attached.append(g)
                except ValueError:
                    continue

    return {
        "success": True,
        "ok": True,
        "project": proj.schema_name,
        "repo": proj.slug,
        "title": proj.title,
        "groups_attached": attached,
        "message": f"项目 [{proj.slug}] 研发台账空间已成功开通！物理 Schema: [{proj.schema_name}]"
                   + (f"；已挂载群组: {attached} (组内互见互写, 跨组不可见)" if attached else "；当前为 legacy 开放态 (未挂组)"),
    }


# =============================================================================
# 一·五、 多用户分组权限管理面 (0.5.0, admin 门禁)
# =============================================================================

@mcp.tool()
@tool_shell
async def group_record(
    group_name: str,
    description: str | None = None,
) -> dict:
    """创建或更新群组 (admin 门禁)。群组是项目可见性的唯一边界: 组内互见互写, 跨组不可见。

    - group_name: 必填。群组名 (2~64 位字母数字下划线中划线)。
    - description: 可选。群组职责说明。
    """
    await _require_admin("group_record")
    name = sanitize_text(group_name).clean_text.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,63}", name):
        return tool_error("INVALID_ARGUMENT", "group_name 需为 2~64 位字母/数字/下划线/中划线。", tool="group_record")
    identity = os.getenv("LOGBOOK_AGENT_ID", "").strip() or None
    g = await db.create_group(name, description, identity)
    return {"success": True, "ok": True, "group": g}


@mcp.tool()
@tool_shell
async def group_grant(group_name: str, project: str) -> dict:
    """将项目挂载到群组 (admin 门禁)。挂载即收口: 未挂组用户立即对该项目不可见不可写。"""
    await _require_admin("group_grant")
    name = sanitize_text(group_name).clean_text.strip()
    gname = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,63}", name)
    if not gname:
        return tool_error("INVALID_ARGUMENT", "group_name 非法。", tool="group_grant")
    proj = await validate_and_negotiate_project(project, is_write=True, allow_cross_project=True)
    identity = os.getenv("LOGBOOK_AGENT_ID", "").strip() or None
    await db.grant_group_project(name, proj, identity)
    await db.reset_data_pool()
    return {
        "success": True, "ok": True,
        "group": name, "project": proj,
        "message": f"项目 [{proj}] 已挂载到群组 [{name}]: 组内互见互写, 跨组不可见 (授权已即时生效)。",
    }


@mcp.tool()
@tool_shell
async def group_revoke(group_name: str, project: str) -> dict:
    """将项目从群组摘除 (admin 门禁)。摘后若无任何挂载组, 项目回到 legacy 开放态。"""
    await _require_admin("group_revoke")
    name = sanitize_text(group_name).clean_text.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,63}", name):
        return tool_error("INVALID_ARGUMENT", "group_name 非法。", tool="group_revoke")
    proj = await validate_and_negotiate_project(project, is_write=True, allow_cross_project=True)
    identity = os.getenv("LOGBOOK_AGENT_ID", "").strip() or None
    await db.revoke_group_project(name, proj, identity)
    await db.reset_data_pool()
    return {"success": True, "ok": True, "group": name, "project": proj,
            "message": f"项目 [{proj}] 已从群组 [{name}] 摘除。"}


@mcp.tool()
@tool_shell
async def group_member_add(group_name: str, agent_name: str) -> dict:
    """将账户加入群组 (admin 门禁)。账户不存在时自动注册。"""
    await _require_admin("group_member_add")
    name = sanitize_text(group_name).clean_text.strip()
    agent = sanitize_text(agent_name).clean_text.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{1,63}", name):
        return tool_error("INVALID_ARGUMENT", "group_name 非法。", tool="group_member_add")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", agent):
        return tool_error("INVALID_ARGUMENT", "agent_name 非法。", tool="group_member_add")
    identity = os.getenv("LOGBOOK_AGENT_ID", "").strip() or None
    await db.add_group_member(name, agent, identity)
    await db.reset_data_pool()
    return {"success": True, "ok": True, "group": name, "agent": agent,
            "message": f"账户 [{agent}] 已加入群组 [{name}], 立即获得该群组全部挂载项目的读写权。"}


@mcp.tool()
@tool_shell
async def group_member_remove(group_name: str, agent_name: str) -> dict:
    """将账户移出群组 (admin 门禁)。移除后对该群组挂载项目立即不可见。"""
    await _require_admin("group_member_remove")
    name = sanitize_text(group_name).clean_text.strip()
    agent = sanitize_text(agent_name).clean_text.strip()
    identity = os.getenv("LOGBOOK_AGENT_ID", "").strip() or None
    removed = await db.remove_group_member(name, agent, identity)
    await db.reset_data_pool()
    if not removed:
        return tool_error("MEMBERSHIP_NOT_FOUND", f"账户 [{agent}] 不在群组 [{name}] 中。", tool="group_member_remove")
    return {"success": True, "ok": True, "group": name, "agent": agent,
            "message": f"账户 [{agent}] 已移出群组 [{name}]。"}


@mcp.tool()
@tool_shell
async def group_query() -> dict:
    """查询全部群组及成员/挂载项目 (所有认证账户可读, 便于感知协作边界)。"""
    groups = await db.list_groups()
    return {"success": True, "ok": True, "total": len(groups), "groups": groups}


@mcp.tool()
@tool_shell
async def principal_query() -> dict:
    """查询当前身份的权限画像: 账户、admin 标记、所在群组。"""
    identity, err = _trusted_agent_identity(None)
    if err:
        return err
    try:
        is_admin = await db.is_admin(identity)
    except Exception:
        is_admin = False
    try:
        groups = await db.get_principal_groups(identity)
    except Exception:
        groups = []
    return {
        "success": True, "ok": True,
        "identity": identity,
        "is_admin": is_admin,
        "groups": groups,
        "semantics": "组内互见互写, 跨组不可见; 未挂组项目为 legacy 开放态。",
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
    reviewer: str | None = "zcode",
    agent_ip: str | None = None,
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
        * 'review': 待验收 (codebuddy 施工完成，等待 reviewer 检验)
        * 'blocked': 阻塞中 (等待用户裁决或外部依赖)
        * 'closed': 办结闭环 (必须带 commit_hash 或 proof_link 真实证据)
        * 'wontfix': 经评估不予修复并备案
    - task_type: 工程类型 ('feat', 'fix', 'verify', 'investigation', 'drill', 'docs', 'ops', 'deploy')。
    - priority: 优先级 ('P0'=阻塞故障, 'P1'=核心严重, 'P2'=中度演练, 'P3'=轻微文档)。
    - assignee: 责任人锁 (防止多 Agent 争抢，如 'agy', 'agent_xxx')。
    - reviewer: 验收责任人 (如 'zcode', 'agy')。
    - agent_ip: 可选。操作 Agent 节点 IP 地址 (缺省自动探测填充)。
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

    caller_ip = detect_caller_ip(agent_ip)

    task = Task(
        id=id,
        title=s_title,
        task_type=TaskType(tt_val),
        priority=TaskPriority(pr_val),
        status=TaskStatus(st_val),
        assignee=assignee,
        reviewer=reviewer,
        agent_ip=caller_ip,
        parent_id=parent_id,
        commit_hash=commit_hash,
        proof_link=proof_link,
        notes=s_notes,
        tags=tags or [],
        batch_id=batch_id,
    )
    saved = await db.upsert_task(proj, task, operator=assignee or "agy")
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
    agent_ip: str | None = None,
    allow_cross_project: bool = False,
    full: bool = False,
) -> dict:
    """原子批量登记或更新任务台账 (单事务批量落库，大幅压减网络 RTT 往返开销)。

    参数说明:
    - project: 必填。项目代号。
    - tasks: 必填。任务字典列表，单项包含 id, title, status 等。
    - batch_id: 可选。统一归属研发批次号 (若任务项本身未指定，则以此默认填充)。
    - agent_ip: 可选。批量操作的 Agent 节点 IP 地址 (缺省自动探测填充)。
    - allow_cross_project: 显式放行跨项目写入授权 (默认 False)。
    - full: 可选。True 时回显 items 全对象列表 (缺省 False 仅回瘦身 {ok, total, ids})。
    """
    try:
        proj = await validate_and_negotiate_project(project, allow_cross_project=allow_cross_project)
    except ProjectNegotiationError as e:
        return e.to_dict()

    caller_ip = detect_caller_ip(agent_ip)

    # 若指定了统一批次号，自动保底确保 batches 存在以满足外键约束
    if batch_id:
        await db.upsert_batch(proj, Batch(id=batch_id, title=f"研发批次 {batch_id}", status=BatchStatus.RUNNING, agent_ip=caller_ip))

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
            reviewer=t.get("reviewer", "zcode"),
            agent_ip=t.get("agent_ip") or caller_ip,
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
    agent_ip: str | None = None,
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
    - agent_ip: 可选。上报缺陷的 Agent 节点 IP 地址 (缺省自动探测填充)。
    - allow_cross_project: 显式放行跨项目写入授权 (默认 False)。
    - task_id 写前预检: 关联任务不存在时回 TASK_NOT_FOUND 结构化错。
    - full: 可选。True 时回显发现项全对象 (缺省 False 仅回瘦身 {ok, id, status})。
    """
    try:
        proj = await validate_and_negotiate_project(project, allow_cross_project=allow_cross_project)
    except ProjectNegotiationError as e:
        return e.to_dict()

    caller_ip = detect_caller_ip(agent_ip)

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
        agent_ip=caller_ip,
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
    agent_ip: str | None = None,
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
    - agent_ip: 可选。登记或更新待办的 Agent 节点 IP 地址 (缺省自动探测填充)。
    - allow_cross_project: 显式放行跨项目写入授权 (默认 False)。
    - full: 可选。True 时回显待办全对象 (缺省 False 仅回瘦身 {ok, id, status})。
    """
    try:
        proj = await validate_and_negotiate_project(project, allow_cross_project=allow_cross_project)
    except ProjectNegotiationError as e:
        return e.to_dict()

    caller_ip = detect_caller_ip(agent_ip)

    cat_val = category.value if isinstance(category, WaitingCategory) else normalize_waiting_category(category)

    waiting = Waiting(
        id=id,
        category=WaitingCategory(cat_val),
        owner=owner,
        agent_ip=caller_ip,
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
    agent_ip: str | None = None,
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
    - agent_ip: 可选。撰写手记的 Agent 节点 IP 地址 (缺省自动探测填充)。
    - task_id: 关联任务编号。
    - visibility: 密级 ('project_private'=项目私有, 'public_safe'=全局开源脱敏)。
    - id: 可选。指定排查手记 ID 执行幂等更新。
    - allow_cross_project: 显式放行跨项目写入授权 (默认 False)。
    """
    try:
        proj = await validate_and_negotiate_project(project, allow_cross_project=allow_cross_project)
    except ProjectNegotiationError as e:
        return e.to_dict()

    caller_ip = detect_caller_ip(agent_ip)

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
                agent_ip=caller_ip,
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
                agent_ip=caller_ip,
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
        agent_ip=caller_ip,
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
async def rule_record(
    id: str,
    title: str,
    summary: str,
    bad_practice: str,
    good_practice: str,
    category: Literal["general", "network", "kernel", "database", "security", "engineering", "governance", "resource"] = "general",
    constraints: str | None = None,
    full: bool = False,
) -> dict:
    """登记或更新架构铁律/工程红线 (SSOT)。全项目共享，按 id 幂等更新。

    标准要素 (AGENTS.md 正典):
    - id: 必填。规则标识 (如 'RULE-NET-01')。
    - title: 必填。规则标题。
    - summary: 必填。一句话核心原则。
    - bad_practice: 必填。错误示范 (Anti-Pattern)。
    - good_practice: 必填。正确做法 (Best Practice)。
    - category: 领域分类 (general/network/kernel/database/security/engineering/governance/resource)。
    - constraints: 可选。执行细则与边界红线说明。
    - full: 可选。True 时回显规则全对象 (缺省 False 仅回瘦身 {ok, id, category})。
    """
    rule = Rule(
        id=id,
        category=category,
        title=sanitize_text(title).clean_text,
        summary=sanitize_text(summary).clean_text,
        bad_practice=sanitize_text(bad_practice).clean_text,
        good_practice=sanitize_text(good_practice).clean_text,
        constraints=sanitize_text(constraints).clean_text if constraints else None,
    )
    saved = await db.upsert_rule(rule)
    slim = {"success": True, "ok": True, "id": id, "category": saved.category}
    if full:
        slim["rule"] = saved.model_dump(mode="json")
    return slim


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
    agent_ip: str | None = None,
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
    - agent_ip: 可选。开立或维护批次的 Agent 节点 IP 地址 (缺省自动探测填充)。
    - allow_cross_project: 显式放行跨项目写入授权 (默认 False)。
    - full: 可选。True 时回显批次全对象 (缺省 False 仅回瘦身 {ok, id, status})。
    """
    try:
        proj = await validate_and_negotiate_project(project, allow_cross_project=allow_cross_project)
    except ProjectNegotiationError as e:
        return e.to_dict()

    caller_ip = detect_caller_ip(agent_ip)

    batch = Batch(
        id=id,
        title=title,
        status=BatchStatus(status),
        branch_name=branch_name,
        summary=summary,
        methodology_notes=methodology_notes,
        agent_ip=caller_ip,
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

    try:
        mailbox_summary = await db.get_mailbox_summary(proj)
    except Exception:
        mailbox_summary = []

    # 多用户: 本人未读角标 (信箱 RLS 下 summary 仅含本人; legacy 模式保持全量概览)
    me_unread = None
    me = os.getenv("LOGBOOK_AGENT_ID", "").strip()
    if me:
        try:
            me_unread = await db.count_agent_unread(me, proj)
            lines[0] += f" my_unread={me_unread}"
        except Exception:
            me_unread = None

    unread_msgs_total = sum(m.get("unread_count", 0) for m in mailbox_summary)
    if mailbox_summary:
        lines[0] += f" mailbox_unread={unread_msgs_total}"
        for m in mailbox_summary:
            ids_preview = ",".join(str(i) for i in (m.get("unread_ids") or [])[:5])
            lines.append(f"M: {m.get('to_agent')} unread={m.get('unread_count')} ids=[{ids_preview}]")
    data["mailbox_summary"] = mailbox_summary
    if me_unread is not None:
        data["my_unread"] = me_unread

    return {
        "success": True,
        "ok": True,
        "project": proj,
        "text": "\n".join(lines),
        "brief": data,
    }


# =============================================================================
# 七、 多 Agent 对讲信箱与代码文件租约工具集 (0.3.0)
# =============================================================================

@mcp.tool()
@tool_shell
async def message_send(
    project: str,
    from_agent: str,
    to_agent: str,
    subject: str,
    content: str,
    from_ip: str | None = None,
    to_ip: str | None = None,
    task_id: str | None = None,
    thread_id: str | None = None,
    allow_cross_project: bool = False,
    full: bool = False,
) -> dict:
    """向目标 Agent 发送对讲信件或任务指派通知。

    字段说明:
    - project: 必填。归属项目代号。
    - from_agent: 必填。发件人 Agent 名称 (如 'codebuddy', 'agy')。
    - to_agent: 必填。收件人 Agent 名称 (如 'zcode', 'codebuddy')。
    - subject: 必填。信件主题 (256字符内)。
    - content: 必填。消息正文 (支持 Markdown)。
    - from_ip: 可选。发件端节点 IP 地址 (缺省自动探测并填充当前节点 IP)。
    - to_ip: 可选。目标节点 IP 地址 (缺省默认与 from_ip 保持一致)。
    - task_id: 可选。关联的具体任务编号 (如 'L13', 'T101')。
    - thread_id: 可选。会话线索短标识。
    - full: 可选。True 时回显全量 Message 实体。
    """
    trusted_identity, identity_error = _trusted_agent_identity(from_agent)
    if identity_error:
        return identity_error
    proj = await validate_and_negotiate_project(project, allow_cross_project=allow_cross_project)
    s_sub = sanitize_text(subject).clean_text
    s_cnt = sanitize_text(content).clean_text

    clean_from_ip = detect_caller_ip(from_ip)
    clean_to_ip = (to_ip or "").strip()
    if not clean_to_ip:
        clean_to_ip = clean_from_ip

    msg = await asyncio.shield(
        db.send_agent_message(
            project_id=proj,
            from_agent=trusted_identity,
            to_agent=to_agent.strip(),
            subject=s_sub,
            content=s_cnt,
            task_id=task_id.strip() if task_id else None,
            thread_id=thread_id.strip() if thread_id else None,
            from_ip=clean_from_ip,
            to_ip=clean_to_ip,
        )
    )
    # 收件人注册核验: 未注册账户仍可投递 (信任既有开放语义), 但显式告警防静默丢失
    try:
        recipient_registered = await db.principal_exists(to_agent.strip())
    except Exception:
        recipient_registered = None

    slim = {
        "success": True,
        "ok": True,
        "project": proj,
        "id": msg.id,
        "from_agent": msg.from_agent,
        "to_agent": msg.to_agent,
        "from_ip": msg.from_ip,
        "to_ip": msg.to_ip,
        "status": "sent",
        "recipient_registered": recipient_registered,
    }
    if recipient_registered is False:
        slim["warning"] = f"收件人 [{msg.to_agent}] 尚未注册 logbook 账户, 信件已投递但对方可能永远无法查收。"
    if full:
        slim["message"] = msg.model_dump(mode="json")
    return slim


@mcp.tool()
@tool_shell
async def agent_message_send(
    project: str,
    from_agent: str,
    to_agent: str,
    subject: str,
    content: str,
    from_ip: str | None = None,
    to_ip: str | None = None,
    task_id: str | None = None,
    thread_id: str | None = None,
    allow_cross_project: bool = False,
    full: bool = False,
) -> dict:
    """向目标 Agent 发送对讲信件或任务指派通知 (message_send 别名)。"""
    return await message_send(
        project=project,
        from_agent=from_agent,
        to_agent=to_agent,
        subject=subject,
        content=content,
        from_ip=from_ip,
        to_ip=to_ip,
        task_id=task_id,
        thread_id=thread_id,
        allow_cross_project=allow_cross_project,
        full=full,
    )


@mcp.tool()
@tool_shell
async def message_inbox(
    agent_name: str,
    project: str | None = None,
    unread_only: bool = True,
    limit: int = 20,
    include_archived: bool = False,
    allow_cross_project: bool = True,
) -> dict:
    """查收指定 Agent 的独立信箱 (未读/全部消息)。

    字段说明:
    - agent_name: 必填。要查收信箱的 Agent 身份名称 (如 'zcode')。
    - project: 可选。指定项目过滤 (缺省为查收该 Agent 的全部跨项目信箱)。
    - unread_only: 可选。默认 True (仅查未读)。False 时查收历史全部。
    - limit: 可选。返回条数限制 (默认 20，上限 200)。
    - include_archived: 可选。默认 False (归档信件不出现在收件箱)。
    """
    trusted_identity, identity_error = _trusted_agent_identity(agent_name)
    if identity_error:
        return identity_error
    proj = None
    if project:
        proj = await validate_and_negotiate_project(project, allow_cross_project=allow_cross_project)

    msgs = await db.get_agent_inbox(
        project_id=proj,
        agent_name=trusted_identity,
        unread_only=unread_only,
        limit=limit,
        include_archived=include_archived,
    )
    items = [m.model_dump(mode="json") for m in msgs]
    return {
        "success": True,
        "ok": True,
        "agent": trusted_identity,
        "total": len(items),
        "messages": items,
    }


@mcp.tool()
@tool_shell
async def message_read(
    message_id: int,
    agent_name: str | None = None,
) -> dict:
    """阅读指定编号的信件详情，并原子标记为已读。

    字段说明:
    - message_id: 必填。信件全局唯一自增 ID。
    - agent_name: 可选兼容参数，只能等于可信调用身份。
    """
    trusted_identity, identity_error = _trusted_agent_identity(agent_name)
    if identity_error:
        return identity_error
    msg = await db.read_agent_message(
        message_id=message_id,
        agent_name=trusted_identity,
    )
    if not msg:
        return tool_error("MESSAGE_NOT_READABLE", "信件不存在或当前身份无权读取")
    return {
        "success": True,
        "ok": True,
        "message": msg.model_dump(mode="json"),
    }


@mcp.tool()
@tool_shell
async def message_reply(
    message_id: int,
    content: str,
    subject: str | None = None,
    from_ip: str | None = None,
    to_ip: str | None = None,
) -> dict:
    """回复信件: 沿用父信的项目/任务/会话线索, 收件人自动取对方 (点对点, 仅收发双方可见)。

    字段说明:
    - message_id: 必填。要回复的父信 ID (必须为您的收信或发信)。
    - content: 必填。回复正文 (Markdown)。
    - subject: 可选。缺省自动带 "Re: " 前缀。
    """
    trusted_identity, identity_error = _trusted_agent_identity(None)
    if identity_error:
        return identity_error
    s_cnt = sanitize_text(content).clean_text
    s_sub = sanitize_text(subject).clean_text.strip() if subject else None
    clean_from_ip = detect_caller_ip(from_ip)
    clean_to_ip = (to_ip or "").strip() or clean_from_ip
    msg = await asyncio.shield(
        db.reply_agent_message(
            message_id=message_id,
            me=trusted_identity,
            content=s_cnt,
            subject=s_sub,
            from_ip=clean_from_ip,
            to_ip=clean_to_ip,
        )
    )
    if not msg:
        return tool_error("MESSAGE_NOT_FOUND", "父信不存在或您不在其收发双方之列 (跨组/跨人不可见)")
    return {
        "success": True,
        "ok": True,
        "id": msg.id,
        "thread_id": msg.thread_id,
        "to_agent": msg.to_agent,
        "message": msg.model_dump(mode="json"),
    }


@mcp.tool()
@tool_shell
async def message_thread(
    thread_id: str,
    project: str | None = None,
    limit: int = 50,
) -> dict:
    """拉取会话线索全部信件 (时间正序, 仅您为收发双方的信件可见)。"""
    trusted_identity, identity_error = _trusted_agent_identity(None)
    if identity_error:
        return identity_error
    proj = None
    if project:
        proj = await validate_and_negotiate_project(project, is_write=False, allow_cross_project=True)
    msgs = await db.get_agent_thread(
        thread_id=thread_id.strip(),
        agent_name=trusted_identity,
        project_id=proj,
        limit=clamp_limit(limit),
    )
    return {
        "success": True,
        "ok": True,
        "thread_id": thread_id,
        "total": len(msgs),
        "messages": [m.model_dump(mode="json") for m in msgs],
    }


@mcp.tool()
@tool_shell
async def message_sent(
    project: str | None = None,
    limit: int = 20,
) -> dict:
    """已发送信箱 (本人发件记录)。"""
    trusted_identity, identity_error = _trusted_agent_identity(None)
    if identity_error:
        return identity_error
    proj = None
    if project:
        proj = await validate_and_negotiate_project(project, is_write=False, allow_cross_project=True)
    msgs = await db.get_agent_sent(
        agent_name=trusted_identity,
        project_id=proj,
        limit=clamp_limit(limit),
    )
    return {
        "success": True,
        "ok": True,
        "total": len(msgs),
        "messages": [m.model_dump(mode="json") for m in msgs],
    }


@mcp.tool()
@tool_shell
async def message_search(
    query: str,
    project: str | None = None,
    limit: int = 20,
) -> dict:
    """全文检索本人收发信件 (主题+正文模糊匹配, 仅可见自己的信件)。"""
    trusted_identity, identity_error = _trusted_agent_identity(None)
    if identity_error:
        return identity_error
    proj = None
    if project:
        proj = await validate_and_negotiate_project(project, is_write=False, allow_cross_project=True)
    msgs = await db.search_agent_messages(
        agent_name=trusted_identity,
        query=sanitize_text(query).clean_text,
        project_id=proj,
        limit=clamp_limit(limit),
    )
    return {
        "success": True,
        "ok": True,
        "total": len(msgs),
        "messages": [m.model_dump(mode="json") for m in msgs],
    }


@mcp.tool()
@tool_shell
async def message_archive(
    message_id: int,
    archived: bool = True,
) -> dict:
    """归档/恢复信件 (仅收件人可操作; 归档后移出未读收件箱, 不影响对方)。"""
    trusted_identity, identity_error = _trusted_agent_identity(None)
    if identity_error:
        return identity_error
    msg = await db.archive_agent_message(
        message_id=message_id,
        agent_name=trusted_identity,
        archived=archived,
    )
    if not msg:
        return tool_error("MESSAGE_NOT_ARCHIVABLE", "信件不存在或您不是其收件人")
    return {
        "success": True,
        "ok": True,
        "id": msg.id,
        "is_archived": msg.is_archived,
        "message": f"信件 [{msg.id}] 已{'归档' if archived else '恢复'}。",
    }


@mcp.tool()
@tool_shell
async def lease_acquire(
    project: str,
    agent_name: str,
    file_path: str,
    duration_seconds: int = 300,
    agent_ip: str | None = None,
    allow_cross_project: bool = False,
) -> dict:
    """申请代码文件修改租约 (防并发写冲突软锁)。

    字段说明:
    - project: 必填。项目代号。
    - agent_name: 必填。申请锁的 Agent 名称 (如 'codebuddy')。
    - file_path: 必填。目标代码文件相对路径 (如 'src/logbook/db.py')。
    - duration_seconds: 可选。租约持有秒数 (默认 300 秒/5分钟，上限 3600 秒)。
    - agent_ip: 可选。申请租约的 Agent 节点 IP 地址 (缺省自动探测填充)。
    """
    trusted_identity, identity_error = _trusted_agent_identity(agent_name)
    if identity_error:
        return identity_error
    proj = await validate_and_negotiate_project(project, allow_cross_project=allow_cross_project)
    clean_path = file_path.strip().lstrip("/")
    caller_ip = detect_caller_ip(agent_ip)

    ok, lease, conflict_agent = await db.acquire_file_lease(
        project_id=proj,
        agent_name=trusted_identity,
        file_path=clean_path,
        duration_seconds=duration_seconds,
        agent_ip=caller_ip,
    )
    if not ok:
        return {
            "success": False,
            "ok": False,
            "error_type": "LEASE_CONFLICT",
            "detail": f"代码文件 '{clean_path}' 当前已被 Agent [{conflict_agent}] 锁定施工中，请等待其释放或租约过期！",
            "held_by": conflict_agent,
            "expires_at": lease.lease_expires_at.isoformat() if lease else None,
        }

    return {
        "success": True,
        "ok": True,
        "project": proj,
        "agent": trusted_identity,
        "agent_ip": caller_ip,
        "file_path": clean_path,
        "lease_expires_at": lease.lease_expires_at.isoformat() if lease else None,
        "status": "acquired",
    }


@mcp.tool()
@tool_shell
async def lease_release(
    project: str,
    agent_name: str,
    file_path: str,
    allow_cross_project: bool = False,
) -> dict:
    """释放已持有的代码文件修改租约。

    字段说明:
    - project: 必填。项目代号。
    - agent_name: 必填。持锁的 Agent 名称。
    - file_path: 必填。代码文件相对路径。
    """
    trusted_identity, identity_error = _trusted_agent_identity(agent_name)
    if identity_error:
        return identity_error
    proj = await validate_and_negotiate_project(project, allow_cross_project=allow_cross_project)
    clean_path = file_path.strip().lstrip("/")

    released = await db.release_file_lease(
        project_id=proj,
        agent_name=trusted_identity,
        file_path=clean_path,
    )
    return {
        "success": True,
        "ok": True,
        "project": proj,
        "file_path": clean_path,
        "released": released,
    }


@mcp.tool()
@tool_shell
async def lease_query(
    project: str,
    allow_cross_project: bool = False,
) -> dict:
    """查询指定项目当前所有处于生效中的文件修改租约。"""
    proj = await validate_and_negotiate_project(project, allow_cross_project=allow_cross_project)
    leases = await db.query_file_leases(project_id=proj)
    return {
        "success": True,
        "ok": True,
        "project": proj,
        "total": len(leases),
        "leases": [l.model_dump(mode="json") for l in leases],
    }


# =============================================================================
# 八、 研发调研知识库工具集 (0.4.0)
# =============================================================================

@mcp.tool()
@tool_shell
async def research_record(
    project: str,
    title: str,
    objective: str,
    market_landscape: str,
    tradeoffs: str,
    decision: str,
    references: str | None = None,
    doc_no: str | None = None,
    authority: str | None = None,
    legal_level: str | None = None,
    effective_date: str | None = None,
    category: ResearchCategoryArg = ResearchCategory.ARCHITECTURE,
    status: ResearchStatusArg = ResearchStatus.COMPLETED,
    author: str = "agy",
    agent_ip: str | None = None,
    task_id: str | None = None,
    batch_id: str | None = None,
    visibility: Literal["project_private", "public_safe"] = "project_private",
    tags: list[str] | None = None,
    id: int | None = None,
    allow_cross_project: bool = False,
    full: bool = False,
) -> dict:
    """结构化录入或更新研发/政策/智库调研知识库 (支持语义向量与全文检索 + 强制调研五要素 + 自动脱敏)。强制要求显式提供 project 参数。支持 author 与 IP 溯源。

    调研结构化五要素必填项:
    - project: 必填。项目代号。
    - title: 必填。调研主题 (如 'Python 高性能异步 SSH 客户端选型' 或 '数据安全跨境流动管理细则研判')。
    - objective: 必填。【调研目标】业务诉求、问题边界与资源底线。
    - market_landscape: 必填。【成熟方案全景】社区已有方案/国内外政策演进对比。
    - tradeoffs: 必填。【两路线评估与对抗成本】引入 vs 自研代价；各方利益博弈与合规成本。
    - decision: 必填。【最终选型决策/政策建议】选了什么、为什么不用自己写、设计哲学冲突规避策略。
    - references: 可选。【事实依据与文献】官方文档、Benchmark 数据源、法规发文字号。
    - doc_no: 可选。权威字号/标准编号/法典编码 (如 '国发〔2026〕15号', 'RFC 9110')。
    - authority: 可选。发文机构/起草部委/组织/学派 (如 '国家发改委', 'IETF')。
    - legal_level: 可选。效力位阶/标准层级 (如 'law', 'regulation', 'standard')。
    - effective_date: 可选。生效实施日期 (如 '2026-10-01')。
    - category: 分类 (支持技术研发与政策智库分类，如 'architecture', 'policy_macro', 'policy_tech', 'governance' 等)。
    - status: 调研状态 ('completed', 'in_force', 'in_progress', 'draft', 'revised', 'repealed')。
    - author: 调研人或责任 Agent (默认 'agy')。
    - agent_ip: 可选。节点 IP 地址 (缺省自动探测填充)。
    - task_id: 可选。关联任务编号。
    - batch_id: 可选。关联批次编号。
    - visibility: 密级 ('project_private'=项目私有, 'public_safe'=全局开源脱敏)。
    - id: 可选。指定调研编号进行更新。
    - allow_cross_project: 显式放行跨项目写入授权 (默认 False)。
    - full: 可选。True 时回显全量实体。
    """
    try:
        proj = await validate_and_negotiate_project(project, allow_cross_project=allow_cross_project)
    except ProjectNegotiationError as e:
        return e.to_dict()

    caller_ip = detect_caller_ip(agent_ip)

    # 强制脱敏
    s_title = sanitize_text(title).clean_text
    s_obj = sanitize_text(objective).clean_text
    s_ml = sanitize_text(market_landscape).clean_text
    s_to = sanitize_text(tradeoffs).clean_text
    s_dec = sanitize_text(decision).clean_text
    s_ref = sanitize_text(references).clean_text if references else None
    s_doc_no = sanitize_text(doc_no).clean_text if doc_no else None
    s_auth = sanitize_text(authority).clean_text if authority else None
    s_legal = sanitize_text(legal_level).clean_text if legal_level else None
    s_eff = sanitize_text(effective_date).clean_text if effective_date else None

    cat_val = category.value if isinstance(category, ResearchCategory) else normalize_research_category(category)
    st_val = status.value if isinstance(status, ResearchStatus) else normalize_research_status(status)

    # 1. 检查是否存在同任务/同主题调研 (幂等查重与向量缓存复用)
    existing = await db.find_existing_research(proj, research_id=id, task_id=task_id, title=s_title)
    if existing:
        content_unchanged = (
            existing.get("title") == s_title and
            existing.get("objective") == s_obj and
            existing.get("market_landscape") == s_ml and
            existing.get("tradeoffs") == s_to and
            existing.get("decision") == s_dec and
            existing.get("doc_no") == s_doc_no and
            existing.get("authority") == s_auth
        )
        if content_unchanged:
            updated = await db.update_research(
                research_id=existing["id"],
                title=s_title,
                category=cat_val,
                author=author,
                status=st_val,
                objective=s_obj,
                market_landscape=s_ml,
                tradeoffs=s_to,
                decision=s_dec,
                references=s_ref,
                visibility=visibility,
                tags=tags or [],
                embedding=None,
                task_id=task_id,
                batch_id=batch_id,
                agent_ip=caller_ip,
                doc_no=s_doc_no,
                authority=s_auth,
                legal_level=s_legal,
                effective_date=s_eff,
            )
            return {
                "success": True,
                "ok": True,
                "research_id": updated["id"],
                "project": proj,
                "vector_source": "cached_skip",
                "message": f"调研记录 [{updated['id']}] 内容未变化，已更新元数据并复用已有向量缓存"
            }
        else:
            full_text = f"{s_title} {s_doc_no or ''} {s_auth or ''} {s_obj} {s_ml} {s_dec}"
            embed_res = await get_embedding(full_text)
            updated = await db.update_research(
                research_id=existing["id"],
                title=s_title,
                category=cat_val,
                author=author,
                status=st_val,
                objective=s_obj,
                market_landscape=s_ml,
                tradeoffs=s_to,
                decision=s_dec,
                references=s_ref,
                visibility=visibility,
                tags=tags or [],
                embedding=embed_res.embedding,
                task_id=task_id,
                batch_id=batch_id,
                agent_ip=caller_ip,
                doc_no=s_doc_no,
                authority=s_auth,
                legal_level=s_legal,
                effective_date=s_eff,
            )
            return {
                "success": True,
                "ok": True,
                "research_id": updated["id"],
                "project": proj,
                "vector_source": embed_res.source,
                "message": f"调研记录 [{updated['id']}] 内容已更新并重新生成 768 维向量索引"
            }

    # 2. 全新调研入库
    full_text = f"{s_title} {s_doc_no or ''} {s_auth or ''} {s_obj} {s_ml} {s_dec}"
    embed_res = await get_embedding(full_text)

    research = Research(
        project_id=proj,
        task_id=task_id,
        batch_id=batch_id,
        title=s_title,
        category=ResearchCategory(cat_val),
        author=author,
        agent_ip=caller_ip,
        status=ResearchStatus(st_val),
        objective=s_obj,
        market_landscape=s_ml,
        tradeoffs=s_to,
        decision=s_dec,
        references=s_ref,
        doc_no=s_doc_no,
        authority=s_auth,
        legal_level=s_legal,
        effective_date=s_eff,
        visibility=DevLogVisibility(visibility),
        tags=tags or [],
        embedding=embed_res.embedding,
    )
    saved = await db.record_research(research)
    slim = {
        "success": True,
        "ok": True,
        "research_id": saved.id,
        "project": proj,
        "vector_source": embed_res.source,
        "message": f"调研知识已成功入库 [{proj}] 并生成 768 维向量索引"
    }
    if full:
        slim["research"] = saved.model_dump(mode="json")
    return slim


@mcp.tool()
@tool_shell
async def research_search(
    project: str,
    query: str,
    category: str | None = None,
    limit: int = 5,
    fields: list[str] | None = None,
) -> dict:
    """语义向量与全文混合检索跨项目调研知识 (支持研发选型与政策智库)。开工前必查，避免重复调研。返回标准化包装对象。

    - project: 必填。当前项目代号。
    - query: 必填。检索关键词、政策主题或技术问题。
    - category: 可选。按分类过滤。
    - limit: 返回条数上限 (1..200, 缺省 5, 超界自动钳制)。
    - fields: 可选列投影，缺省回全列。
    """
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
    except ProjectNegotiationError as e:
        return e.to_dict()

    limit = clamp_limit(limit, maximum=QUERY_LIMIT_MAX)
    s_query = sanitize_text(query).clean_text
    embed_res = await get_embedding(s_query)

    results = await db.search_researches(
        project=proj,
        query_vector=embed_res.embedding,
        query_text=s_query if not embed_res.embedding else None,
        category=category,
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
async def research_query(
    project: str,
    status: str | None = None,
    category: str | None = None,
    authority: str | None = None,
    doc_no: str | None = None,
    limit: int = 20,
    fields: list[str] | None = None,
) -> dict:
    """列表与看板查询调研记录 (支持发文机关、字号、分类与状态过滤)。返回标准化包装对象。

    - project: 必填。项目代号。
    - status: 可选。调研/政策状态过滤。
    - category: 可选。分类过滤。
    - authority: 可选。发文机关/起草部委过滤。
    - doc_no: 可选。权威字号/标准编号过滤。
    - limit: 返回条数上限 (1..200, 缺省 20, 超界自动钳制)。
    - fields: 可选列投影，缺省回全列。
    """
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
    except ProjectNegotiationError as e:
        return e.to_dict()

    limit = clamp_limit(limit)
    st_enum = ResearchStatus(normalize_research_status(status)) if status else None
    cat_enum = ResearchCategory(normalize_research_category(category)) if category else None

    researches = await db.query_researches(
        proj,
        status=st_enum,
        category=cat_enum,
        authority=authority,
        doc_no=doc_no,
        limit=limit
    )
    items = project_fields([r.model_dump(mode="json") for r in researches], fields)
    return {
        "success": True,
        "project": proj,
        "total": len(items),
        "items": items
    }


# =============================================================================
# 通用事实引擎与数据集管理 (0.5.0)
# =============================================================================

@mcp.tool()
@tool_shell
async def dataset_upsert(
    code: str,
    name: str,
    category: str = "general",
    field_schema: list[dict[str, Any]] | None = None,
    alias_map: dict[str, str] | None = None,
    description: str | None = None,
) -> dict:
    """注册或更新数据集元数据规范与字段定义 (Schema & Metadata).

    - code: 必填。数据集唯一标识码 (2~64 位小写字母、数字、点或下划线，如 'safe_forex', 'oss_benchmark')。
    - name: 必填。数据集全称或中文名称 (如 '人行涉外收汇申报')。
    - category: 可选。业务领域分类: finance, oss_metrics, trade, tax, research, general。
    - field_schema: 可选。字段元数据定义列表 [{"key": "...", "label": "...", "type": "..."}]。
    - alias_map: 可选。历史版本与同义词映射表 {"结汇金额": "amount", "申报号": "doc_no"}。
    - description: 可选。数据集口径与填报业务说明。
    """
    cat = normalize_dataset_category(category)
    ds = Dataset(
        code=code,
        name=name,
        category=cat,
        field_schema=field_schema or [],
        alias_map=alias_map or {},
        description=description,
    )
    result = await db.upsert_dataset(ds)
    for k in ("created_at", "updated_at"):
        if isinstance(result.get(k), (datetime, date)):
            result[k] = result[k].isoformat()
    return {
        "success": True,
        "action": "upserted",
        "dataset": result,
    }


@mcp.tool()
@tool_shell
async def dataset_query(
    code: str | None = None,
    category: str | None = None,
    limit: int = 50,
) -> dict:
    """查询数据集定义元数据字典列表或详情。

    - code: 可选。指定唯一编码精准检索单条数据集规范。
    - category: 可选。按业务领域分类过滤: finance, oss_metrics, trade, tax, research, general。
    - limit: 返回条数上限 (1..200, 缺省 50)。
    """
    if code:
        res = await db.get_dataset(code)
        if res:
            for k in ("created_at", "updated_at"):
                if isinstance(res.get(k), (datetime, date)):
                    res[k] = res[k].isoformat()
        return {
            "success": True,
            "total": 1 if res else 0,
            "items": [res] if res else [],
        }
    cat = normalize_dataset_category(category) if category else None
    items = await db.list_datasets(category=cat, limit=clamp_limit(limit))
    for it in items:
        for k in ("created_at", "updated_at"):
            if isinstance(it.get(k), (datetime, date)):
                it[k] = it[k].isoformat()
    return {
        "success": True,
        "total": len(items),
        "items": items,
    }


@mcp.tool()
@tool_shell
async def record_upsert(
    project: str,
    dataset_code: str,
    doc_no: str,
    event_date: str,
    amount: float | str | int = 0,
    currency: str = "CNY",
    entity_name: str | None = None,
    status: str = "recorded",
    payload: dict[str, Any] | None = None,
    parent_id: int | None = None,
    batch_id: str | None = None,
    id: int | None = None,
) -> dict:
    """写入或更新通用业务/事实单据记录 (Strong Spine + Flexible Ribs)。

    - project: 必填。归属项目代号 (必须显式传参)。
    - dataset_code: 必填。关联的数据集定义编码 (如 'safe_forex', 'oss_benchmark')。
    - doc_no: 必填。单据号/唯一标识 (发票号、关单号、跑分流水号)。
    - event_date: 必填。业务发生日期或测试日期 (YYYY-MM-DD)。
    - amount: 可选。核心数值/金额 (高精度 NUMERIC, 缺省 0)。
    - currency: 可选。币种或量纲单位 (USD, CNY, QPS, MS 等，缺省 CNY)。
    - entity_name: 可选。往来单位/境外付款人/组件仓库名。
    - status: 可选。状态: recorded, reconciled, verified, cancelled (缺省 recorded)。
    - payload: 可选。业务特有动态属性载荷 (JSONB 软肋骨)。
    - parent_id: 可选。父单据记录 ID (支持主子表分录)。
    - batch_id: 可选。导入批次标识。
    - id: 可选。指定物理记录 ID 更新。
    """
    try:
        proj = await validate_and_negotiate_project(project, is_write=True)
    except ProjectNegotiationError as e:
        return e.to_dict()

    st = normalize_record_status(status)
    record = DataRecord(
        id=id,
        project_id=proj,
        dataset_code=dataset_code.strip().lower(),
        parent_id=parent_id,
        batch_id=batch_id,
        doc_no=doc_no.strip(),
        entity_name=entity_name.strip() if entity_name else None,
        amount=amount,
        currency=currency.strip().upper() if currency else "CNY",
        event_date=event_date,
        status=st,
        payload=payload or {},
    )
    res = await db.upsert_record(record)
    for k in ("event_date", "created_at", "updated_at"):
        if isinstance(res.get(k), (datetime, date)):
            res[k] = res[k].isoformat()
    if "amount" in res:
        res["amount"] = float(res["amount"])
    return {
        "success": True,
        "action": "upserted",
        "record": res,
    }


@mcp.tool()
@tool_shell
async def record_query(
    project: str,
    dataset_code: str | None = None,
    doc_no: str | None = None,
    entity_name: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    amount_min: float | None = None,
    amount_max: float | None = None,
    status: str | None = None,
    batch_id: str | None = None,
    parent_id: int | None = None,
    payload_filter: dict[str, Any] | None = None,
    query_text: str | None = None,
    limit: int = 50,
    offset: int = 0,
    fields: list[str] | None = None,
) -> dict:
    """多维检索事实数据单据 (支持日期/金额区间、全文检索、JSONB 属性包含)。

    - project: 必填。项目代号。
    - dataset_code: 可选。数据集编码过滤。
    - doc_no: 可选。单号精准检索。
    - entity_name: 可选。往来单位模糊匹配。
    - date_from / date_to: 可选。业务发生日期范围 (YYYY-MM-DD)。
    - amount_min / amount_max: 可选。核心数值/金额区间。
    - status: 可选。状态过滤 (recorded, reconciled, verified, cancelled)。
    - batch_id: 可选。批次号过滤。
    - parent_id: 可选。主子表分录父级 ID 过滤。
    - payload_filter: 可选。JSONB 动态属性匹配 (如 {"customs_code": "0101"})。
    - query_text: 可选。全文检索关键词 (跨 doc_no, entity_name 及 payload 全文检索)。
    - limit: 返回条数 (1..200, 缺省 50)。
    - offset: 分页偏移量 (缺省 0)。
    - fields: 可选列投影，缺省返回全列。
    """
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
    except ProjectNegotiationError as e:
        return e.to_dict()

    st = normalize_record_status(status) if status else None
    rows = await db.query_records(
        project_id=proj,
        dataset_code=dataset_code,
        doc_no=doc_no,
        entity_name=entity_name,
        date_from=date_from,
        date_to=date_to,
        amount_min=amount_min,
        amount_max=amount_max,
        status=st,
        batch_id=batch_id,
        parent_id=parent_id,
        payload_filter=payload_filter,
        query_text=query_text,
        limit=clamp_limit(limit),
        offset=offset,
    )
    for r in rows:
        for k in ("event_date", "created_at", "updated_at"):
            if isinstance(r.get(k), (datetime, date)):
                r[k] = r[k].isoformat()
        if "amount" in r:
            r["amount"] = float(r["amount"])

    items = project_fields(rows, fields)
    return {
        "success": True,
        "project": proj,
        "total": len(items),
        "items": items,
    }


@mcp.tool()
@tool_shell
async def record_stats(
    project: str,
    dataset_code: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    group_by: str = "month",
) -> dict:
    """统计与汇总事实单据核心指标 (纯数据库引擎级 SUM/COUNT/AVG 极速聚合)。

    - project: 必填。项目代号。
    - dataset_code: 可选。数据集编码过滤。
    - date_from / date_to: 可选。业务发生日期范围 (YYYY-MM-DD)。
    - group_by: 可选。聚合维度: 'month' (缺省), 'day', 'year', 'currency', 'status', 'entity', 'dataset', 'none'。
    """
    try:
        proj = await validate_and_negotiate_project(project, is_write=False)
    except ProjectNegotiationError as e:
        return e.to_dict()

    stats = await db.aggregate_record_stats(
        project_id=proj,
        dataset_code=dataset_code,
        date_from=date_from,
        date_to=date_to,
        group_by=group_by,
    )
    return {
        "success": True,
        "project": proj,
        "stats": stats,
    }


def run_stdio():
    """以 Stdio 模式运行 MCP Server。"""
    import asyncio as _asyncio
    try:
        _asyncio.run(_session_bootstrap())
    except Exception:
        pass
    mcp.run()


if __name__ == "__main__":
    run_stdio()

