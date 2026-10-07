"""Logbook 智能交互协商与多项目物理隔离守卫模块

功能:
- 强制要求显式 project 参数
- difflib 智能模糊纠错 (Did-You-Mean)
- 物理 Git 工作区事实核验
- 结构化协商响应生成 (促进 Agent 自主纠错与自愈)
"""

import os
import subprocess
import difflib
from pathlib import Path
from .db import db


class ProjectNegotiationError(ValueError):
    """当项目名称不存在或存在拼写疑似错误时抛出的结构化协商异常。"""
    def __init__(self, requested_project: str, suggestions: list[str], workspace_project: str | None, message: str):
        super().__init__(message)
        self.requested_project = requested_project
        self.suggestions = suggestions
        self.workspace_project = workspace_project
        self.message = message

    def to_dict(self) -> dict:
        return {
            "isError": True,
            "error_type": "PROJECT_NEGOTIATION_REQUIRED",
            "requested_project": self.requested_project,
            "suggestions": self.suggestions,
            "workspace_project": self.workspace_project,
            "message": self.message
        }


def detect_current_workspace_project() -> str | None:
    """从调用端物理工作区自动探测 Git 事实源。"""
    env_proj = os.getenv("LOGBOOK_PROJECT")
    if env_proj:
        return env_proj.strip().lower()

    # 1. 优先读取 Git Remote 唯一标识 (如 bpfio/brix.git -> brix)
    try:
        remote = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        slug = remote.split("/")[-1].replace(".git", "").strip()
        if slug:
            return slug.lower()
    except Exception:
        pass

    # 2. 读取 Git 顶级仓库目录名
    try:
        toplevel = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        return Path(toplevel).name.lower()
    except Exception:
        pass

    # 3. 兜底取当前工作目录名
    try:
        return Path(os.getcwd()).name.lower()
    except Exception:
        return None


async def get_registered_projects() -> list[str]:
    """查询数据库中所有已初始化的项目 Schema 列表。"""
    await db.connect()
    async with db._pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT schema_name 
            FROM information_schema.schemata 
            WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'shared', 'public')
              AND schema_name NOT LIKE 'pg_%'
            ORDER BY schema_name;
            """
        )
        return [r["schema_name"].lower() for r in rows]


async def validate_and_negotiate_project(project: str) -> str:
    """强制核验项目身份。若存在拼写错误或越权，触发结构化协商。"""
    if not project or not project.strip():
        raise ValueError("【语法错误】project 参数为必填项，禁止为空！请显式指定目标项目名称。")

    req = project.strip().lower()
    registered = await get_registered_projects()
    workspace_proj = detect_current_workspace_project()

    # 1. 项目不存在 (例如用户或 Agent 把 brix 敲成了 briz) -> 触发智能协商纠错
    if req not in registered:
        # 基于 difflib 计算相近度
        suggestions = difflib.get_close_matches(req, registered, n=3, cutoff=0.6)
        
        # 如果当前工作区项目在候选池中，优先提示
        if workspace_proj and workspace_proj in registered and workspace_proj not in suggestions:
            suggestions.insert(0, workspace_proj)

        suggestion_hint = f"相近合法项目建议: {suggestions}" if suggestions else "当前可用项目列表: " + str(registered)
        workspace_hint = f"当前终端物理工作区为: [{workspace_proj}]" if workspace_proj else "未检测到 Git 物理工作区"

        msg = (
            f"【Logbook 项目协商提示】数据库中未找到项目 '{req}'。\n"
            f"• {suggestion_hint}\n"
            f"• {workspace_hint}\n"
            f"您是否意指 '{suggestions[0] if suggestions else workspace_proj}'？请修正 project 参数后重新调用。"
        )
        raise ProjectNegotiationError(
            requested_project=req,
            suggestions=suggestions,
            workspace_project=workspace_proj,
            message=msg
        )

    # 2. 项目存在，但与当前物理工作区不符 -> 触发物理越权阻断
    if workspace_proj and workspace_proj in registered and req != workspace_proj:
        raise PermissionError(
            f"【Logbook 隔离拦截】越权阻断：当前物理工作区锁定为 [{workspace_proj}]，"
            f"禁止跨项目向 [{req}] 执行写操作或敏感操作！"
        )

    return req
