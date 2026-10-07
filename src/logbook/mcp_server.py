"""Logbook FastMCP Server (Agent Native JSON-RPC 2.0 / Stdio 管道)

特性:
- 0 开放网络端口，原生走系统标准输入输出 (Stdio)
- 暴露 8 大研发正典标准原子工具
- 强制自动脱敏过滤与状态机证据硬锁
"""

import sys
import asyncio
from typing import Any
try:
    from mcp.server.mcpserver import MCPServer
except ImportError:
    from mcp.server.fastmcp import FastMCP as MCPServer

from .models import (
    Task, TaskStatus, TaskPriority, TaskType,
    Finding, FindingStatus, FindingSeverity,
    Waiting, WaitingStatus, WaitingCategory,
    DevLog, DevLogVisibility, Rule
)
from .sanitizer import sanitize_text
from .time_sync import get_beijing_now
from .vector import get_embedding
from .converter import export_devlog_markdown
from .db import db

# 创建 MCP Server 实例
mcp = MCPServer("logbook-mcp-server")


@mcp.tool()
async def task_upsert(
    project: str,
    id: str,
    title: str,
    status: str,
    task_type: str = "fix",
    priority: str = "P2",
    commit_hash: str | None = None,
    proof_link: str | None = None,
    notes: str | None = None,
    batch_id: str | None = None,
) -> dict:
    """原子登记或推进任务状态机。

    参数:
        project: 归属项目 (如 'brix', 'logbook')
        id: 任务短编号 (如 'F14', 'W10')
        title: 任务标题
        status: planned / running / blocked / closed / wontfix
        commit_hash: 提交 Hash (若 status 为 closed 则必填证据锚点)
        proof_link: 验证报告链接或测试输出
    """
    # 自动脱敏
    s_title = sanitize_text(title).clean_text
    s_notes = sanitize_text(notes).clean_text if notes else None

    task = Task(
        id=id,
        title=s_title,
        task_type=TaskType(task_type),
        priority=TaskPriority(priority),
        status=TaskStatus(status),
        commit_hash=commit_hash,
        proof_link=proof_link,
        notes=s_notes,
        batch_id=batch_id,
    )
    saved = await db.upsert_task(project, task)
    return {
        "success": True,
        "task": saved.model_dump(mode="json")
    }


@mcp.tool()
async def task_query(
    project: str,
    status: list[str] | None = None,
    priority: list[str] | None = None,
    batch_id: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """多维查询任务看板。可按状态、优先级过滤。"""
    st_enums = [TaskStatus(s) for s in status] if status else None
    pr_enums = [TaskPriority(p) for p in priority] if priority else None
    tasks = await db.query_tasks(project, status=st_enums, priority=pr_enums, batch_id=batch_id, limit=limit)
    return [t.model_dump(mode="json") for t in tasks]


@mcp.tool()
async def finding_record(
    project: str,
    id: str,
    summary: str,
    source: str = "audit",
    severity: str = "P2",
    status: str = "open",
    task_id: str | None = None,
    resolution: str | None = None,
) -> dict:
    """登记或更新缺陷/审计发现项。"""
    s_summary = sanitize_text(summary).clean_text
    s_resolution = sanitize_text(resolution).clean_text if resolution else None

    finding = Finding(
        id=id,
        source=source,
        severity=FindingSeverity(severity),
        status=FindingStatus(status),
        task_id=task_id,
        summary=s_summary,
        resolution=s_resolution,
    )
    saved = await db.upsert_finding(project, finding)
    return {"success": True, "finding": saved.model_dump(mode="json")}


@mcp.tool()
async def waiting_query(project: str, status: str = "open") -> list[dict]:
    """查询待办与阻塞项 (如待用户决策事项)。"""
    st = WaitingStatus(status)
    waitings = await db.query_waitings(project, status=st)
    return [w.model_dump(mode="json") for w in waitings]


@mcp.tool()
async def devlog_record(
    project: str,
    title: str,
    problem: str,
    root_cause: str,
    solution: str,
    evidence: str,
    task_id: str | None = None,
    visibility: str = "project_private",
    tags: list[str] | None = None,
) -> dict:
    """结构化录入排查手记 (根因四要素 + 自动脱敏 + 512维向量入库)。"""
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
        project_id=project,
        task_id=task_id,
        title=s_title,
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
        "vector_source": embed_res.source,
        "message": "排查手记已成功入库并生成向量索引"
    }


@mcp.tool()
async def devlog_search(
    project: str,
    query: str,
    limit: int = 5,
) -> list[dict]:
    """语义向量与全文混合检索跨 Agent 长期记忆。"""
    s_query = sanitize_text(query).clean_text
    embed_res = await get_embedding(s_query)
    
    # 向量检索或全文降级检索
    results = await db.search_devlogs(
        project=project,
        query_vector=embed_res.embedding,
        query_text=s_query if not embed_res.embedding else None,
        limit=limit
    )
    return results


@mcp.tool()
async def rule_query(keyword: str | None = None) -> list[dict]:
    """开工前对齐架构铁律与工程红线 (SSOT)。"""
    rules = await db.query_rules(keyword=keyword)
    return [r.model_dump(mode="json") for r in rules]


@mcp.tool()
async def export_markdown(project: str, output_path: str = "docs/DEVLOG.md") -> str:
    """从数据库生成完全兼容 brix 格式的 DEVLOG.md。"""
    md = await export_devlog_markdown(project)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(md)
    return f"已成功将项目 {project} 的最新状态导出至 {output_path} (行数: {len(md.splitlines())})"


def run_stdio():
    """以 Stdio 模式运行 MCP Server。"""
    mcp.run()


if __name__ == "__main__":
    run_stdio()
