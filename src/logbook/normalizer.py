"""Logbook 枚举与入参轻量级归一化处理层 (Normalizer)

功能:
- 剥离 Emoji 字符与富文本格式干扰 (如 '✅ closed' -> 'closed', '⏸ blocked' -> 'blocked')
- 常见同义词映射 (如 'done'/'finished' -> 'closed', 'in_progress' -> 'running')
- 任务类型与严重度归一化 (如 'feature' -> 'feat', 'bugfix' -> 'fix')
- 保障 Agent 自动化交互的鲁棒性与容错能力
"""

import re
from typing import Any

# Emoji 与常见 Markdown 符号正则
EMOJI_PATTERN = re.compile(
    r"[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]|[\u2b50-\u2b55]|[\u3030\u303d]|[\ufe0f\u200d]|[\u2022\u25aa\u25ab\u25cf\u25cb]",
    flags=re.UNICODE,
)

# 任务状态同义词映射
STATUS_MAP = {
    "closed": "closed",
    "done": "closed",
    "finish": "closed",
    "finished": "closed",
    "resolved": "closed",
    "completed": "closed",
    "running": "running",
    "in_progress": "running",
    "inprogress": "running",
    "doing": "running",
    "wip": "running",
    "planned": "planned",
    "todo": "planned",
    "plan": "planned",
    "pending": "planned",
    "open": "planned",
    "blocked": "blocked",
    "block": "blocked",
    "hold": "blocked",
    "paused": "blocked",
    "review": "review",
    "in_review": "review",
    "reviewing": "review",
    "wontfix": "wontfix",
    "abandoned": "wontfix",
    "dropped": "wontfix",
    "rejected": "wontfix",
}

# 任务类型同义词映射
TYPE_MAP = {
    "feat": "feat",
    "feature": "feat",
    "new": "feat",
    "fix": "fix",
    "bugfix": "fix",
    "hotfix": "fix",
    "patch": "fix",
    "verify": "verify",
    "test": "verify",
    "validation": "verify",
    "investigation": "investigation",
    "investigate": "investigation",
    "research": "investigation",
    "drill": "drill",
    "exercise": "drill",
    "docs": "docs",
    "doc": "docs",
    "document": "docs",
    "ops": "ops",
    "operation": "ops",
    "maintenance": "ops",
    "deploy": "deploy",
    "release": "deploy",
}

# 优先级映射
PRIORITY_MAP = {
    "p0": "P0",
    "0": "P0",
    "critical": "P0",
    "urgent": "P0",
    "p1": "P1",
    "1": "P1",
    "high": "P1",
    "p2": "P2",
    "2": "P2",
    "medium": "P2",
    "normal": "P2",
    "p3": "P3",
    "3": "P3",
    "low": "P3",
    "minor": "P3",
}

# 发现级别映射 (对齐 P1/P2/P3 规范)
SEVERITY_MAP = {
    "p1": "P1",
    "1": "P1",
    "blocker": "P1",
    "critical": "P1",
    "fatal": "P1",
    "p2": "P2",
    "2": "P2",
    "warn": "P2",
    "warning": "P2",
    "medium": "P2",
    "p3": "P3",
    "3": "P3",
    "info": "P3",
    "information": "P3",
    "note": "P3",
    "low": "P3",
}

# 待办类别映射
WAITING_CATEGORY_MAP = {
    "user": "user",
    "human": "user",
    "decision": "user",
    "external": "external",
    "upstream": "external",
    "closing": "closing",
    "cleanup": "closing",
    "review": "closing",
}


def clean_text(val: Any) -> str:
    """去除首尾空白及 Markdown/Emoji 装饰符号。"""
    if val is None:
        return ""
    text = str(val).strip()
    text = EMOJI_PATTERN.sub("", text).strip()
    # 去除两端可能残留的方括号/圆括号
    text = text.strip("[]() ")
    return text.lower()


def normalize_status(val: Any, default: str = "planned") -> str:
    """将输入状态归一化为正典合法的 Literal 状态。"""
    c = clean_text(val)
    return STATUS_MAP.get(c, default)


def normalize_task_type(val: Any, default: str = "fix") -> str:
    """将输入类型归一化为正典合法的 Literal 任务类型。"""
    c = clean_text(val)
    return TYPE_MAP.get(c, default)


def normalize_priority(val: Any, default: str = "P2") -> str:
    """将输入优先级归一化为 P0/P1/P2/P3。"""
    c = clean_text(val).upper()
    return PRIORITY_MAP.get(c.lower(), default)


def normalize_severity(val: Any, default: str = "P2") -> str:
    """将输入严重度归一化为 P1/P2/P3。"""
    c = clean_text(val).lower()
    return SEVERITY_MAP.get(c, default)


def normalize_waiting_category(val: Any, default: str = "user") -> str:
    """将输入待办类别归一化为 user/external/closing。"""
    c = clean_text(val)
    return WAITING_CATEGORY_MAP.get(c, default)


RESEARCH_CATEGORY_MAP = {
    "architecture": "architecture",
    "arch": "architecture",
    "design": "architecture",
    "framework": "architecture",
    "database": "database",
    "db": "database",
    "storage": "database",
    "sql": "database",
    "network": "network",
    "net": "network",
    "protocol": "network",
    "kernel": "kernel",
    "os": "kernel",
    "bpf": "kernel",
    "ebpf": "kernel",
    "security": "security",
    "sec": "security",
    "auth": "security",
    "library": "library",
    "lib": "library",
    "pkg": "library",
    "package": "library",
    "tooling": "tooling",
    "tool": "tooling",
    "tools": "tooling",
    "ops": "tooling",
    "deploy": "tooling",
}

RESEARCH_STATUS_MAP = {
    "completed": "completed",
    "done": "completed",
    "finish": "completed",
    "finished": "completed",
    "in_progress": "in_progress",
    "running": "in_progress",
    "wip": "in_progress",
    "active": "in_progress",
    "deprecated": "deprecated",
    "abandoned": "deprecated",
    "obsolete": "deprecated",
}


def normalize_research_category(val: Any, default: str = "architecture") -> str:
    """将输入调研分类归一化为正典合法的 ResearchCategory。"""
    c = clean_text(val)
    return RESEARCH_CATEGORY_MAP.get(c, default)


def normalize_research_status(val: Any, default: str = "completed") -> str:
    """将输入调研状态归一化为正典合法的 ResearchStatus。"""
    c = clean_text(val)
    return RESEARCH_STATUS_MAP.get(c, default)

