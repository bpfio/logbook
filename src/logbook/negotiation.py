"""Logbook 智能交互协商与多项目物理隔离守卫模块

功能:
- 强制要求显式 project 参数 (支持 Git 仓库坐标 bpfio/brix 与 短别名 brix 双轨输入)
- 权威 Git 仓库解析与物理 Schema 映射
- difflib 智能模糊纠错 (Did-You-Mean) 与新项目自愈开户引导 (PROJECT_INIT_NEEDED)
- 物理 Git 工作区事实核验与跨项目写操作越权隔离
"""

import os
import re
import subprocess
import difflib
from pathlib import Path
from .db import db
from .errors import NegotiationPermissionError, NegotiationValueError


class ProjectNegotiationError(ValueError):
    """当项目名称不存在、拼写疑似错误或需要自愈开户时抛出的结构化协商异常。"""
    def __init__(
        self,
        requested_project: str,
        suggestions: list[str],
        workspace_project: str | None,
        message: str,
        action_required: str = "CORRECT_PROJECT_PARAMETER"
    ):
        super().__init__(message)
        self.requested_project = requested_project
        self.suggestions = suggestions
        self.workspace_project = workspace_project
        self.message = message
        self.action_required = action_required

    def to_dict(self) -> dict:
        data = {
            "isError": True,
            "error_type": "PROJECT_NEGOTIATION_REQUIRED",
            "action_required": self.action_required,
            "requested_project": self.requested_project,
            "suggestions": self.suggestions,
            "workspace_project": self.workspace_project,
            "message": self.message
        }
        if self.action_required == "PROJECT_INIT_NEEDED":
            data["guidance"] = f"可调用 project_init(repo='{self.requested_project}', title='<项目名称>') 一键初始化专属台账。"
        return data


def detect_current_workspace_project() -> str | None:
    """从调用端物理工作区自动探测 Git 事实源 (优先探测 owner/repo 权威坐标)。"""
    env_proj = os.getenv("LOGBOOK_PROJECT")
    if env_proj:
        return env_proj.strip().lower()

    # 1. 优先提取 Git Remote 权威坐标 (如 bpfio/brix.git -> bpfio/brix, io/TS.git -> io/ts)
    try:
        remote = subprocess.check_output(
            ["git", "config", "--get", "remote.origin.url"],
            stderr=subprocess.DEVNULL, text=True
        ).strip()
        remote_clean = remote.replace(".git", "")
        if ":" in remote_clean and not remote_clean.startswith("http"):
            slug = remote_clean.split(":")[-1].strip("/")
        else:
            parts = [p for p in remote_clean.split("/") if p]
            if len(parts) >= 2:
                slug = f"{parts[-2]}/{parts[-1]}"
            else:
                slug = parts[-1]
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

    # 3. 仅当当前目录显式包含 .git 目录时才提取目录名，避免在非仓库目录 (如 /app, /tmp) 产生误报
    try:
        cwd = Path(os.getcwd())
        if (cwd / ".git").exists():
            return cwd.name.lower()
    except Exception:
        pass

    return None


async def get_registered_projects() -> list[str]:
    """查询数据库中所有已注册项目的别名池 (包含 slug 与 schema_name)。"""
    projs = await db.list_projects()
    names: set[str] = set()
    for p in projs:
        names.add(p.slug.lower())
        names.add(p.schema_name.lower())
    return sorted(list(names))


async def validate_and_negotiate_project(
    project: str,
    is_write: bool = True,
    allow_cross_project: bool = False
) -> str:
    """强制核验项目身份并解析为物理 Schema 名。支持 Git 仓库坐标与短别名双轨解析。"""
    if not project or not project.strip():
        raise NegotiationValueError(
            "【语法错误】project 参数为必填项，禁止为空！请显式指定目标项目名称 (如 'bpfio/brix' 或 'brix')。"
        )

    req = project.strip().lower()

    # 1. 双轨检索：支持 Git 坐标 (bpfio/brix) 与短别名 (brix) 统一解析
    matched = await db.get_project(req)
    workspace_proj = detect_current_workspace_project()

    # 2. 未匹配到任何已注册项目 -> 双态判定 (疑似手滑纠偏 vs 新项目开户指引)
    if not matched:
        all_candidates = await get_registered_projects()
        suggestions = difflib.get_close_matches(req, all_candidates, n=3, cutoff=0.7)

        # 场景 A: 存在高度相似的已注册项目 (如 briz -> brix) -> 判定为疑似输入手滑
        if suggestions:
            msg = (
                f"【Logbook 协商提示】数据库中未找到项目 '{req}'。\n"
                f"• 相近合法项目建议: {suggestions}\n"
                f"您是否意指 '{suggestions[0]}'？请修正 project 参数后重新调用。"
            )
            raise ProjectNegotiationError(
                requested_project=req,
                suggestions=suggestions,
                workspace_project=workspace_proj,
                message=msg,
                action_required="CORRECT_PROJECT_PARAMETER"
            )

        # 场景 B: 无相近项目，确认为全新项目 (如 tax, 8cli/ovh) -> 判定为需要自愈开户
        registered_projs = await db.list_projects()
        active_slugs = [p.slug for p in registered_projs]
        msg = (
            f"【Logbook 开户引导】项目 '{req}' 尚未在 Logbook 中开立台账空间。\n"
            f"• 当前已注册项目: {active_slugs}\n"
            f"如这是一个全新项目，请调用 MCP 原语 'project_init(repo=\"{req}\", title=\"<项目全称>\")' 完成一键建库；\n"
            f"建库完成后即可正常开展任务记录与排查手记沉淀。"
        )
        raise ProjectNegotiationError(
            requested_project=req,
            suggestions=suggestions,
            workspace_project=workspace_proj,
            message=msg,
            action_required="PROJECT_INIT_NEEDED"
        )

    resolved_schema = matched.schema_name

    # 3. 隔离守卫：仅在写操作时校验物理工作区是否匹配 (除非显式声明跨项目写入授权)
    cross_allowed = allow_cross_project or (os.getenv("LOGBOOK_ALLOW_CROSS_PROJECT") == "1")
    if is_write and workspace_proj:
        # workspace_proj 可能是 slug (bpfio/brix) 或目录名 (brix)
        ws_match = await db.get_project(workspace_proj)
        if ws_match:
            ws_schema = ws_match.schema_name
        else:
            from .models import slug_to_schema_name
            ws_schema = slug_to_schema_name(workspace_proj)
        if ws_schema != resolved_schema and not cross_allowed:
            raise NegotiationPermissionError(
                f"【Logbook 隔离拦截】越权阻断：当前物理工作区锁定为 [{workspace_proj}]，"
                f"禁止跨项目向 [{req} -> {resolved_schema}] 执行写操作！"
                f"如确认用户显式要求跨项目操作，请显式传入 allow_cross_project=True。",
                workspace_project=workspace_proj,
                requested_project=req,
            )

    return resolved_schema
