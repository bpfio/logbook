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
    # 叙事工程 / 创作类型
    "outline": "outline",
    "plot": "outline",
    "beats": "outline",
    "draft": "draft",
    "write": "draft",
    "chapter": "draft",
    "polish": "polish",
    "refine": "polish",
    "revise": "polish",
    "continuity": "continuity",
    "audit": "continuity",
    "check": "continuity",
    "lore": "lore",
    "setting": "lore",
    "worldbuilding": "lore",
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


def normalize_status(val: Any, default: str | None = None) -> str:
    """将输入状态归一化为正典合法的 Literal 状态。非法输入且未指定 default 时抛出 ValueError。"""
    if val is None or str(val).strip() == "":
        if default is not None:
            return default
        raise ValueError("status 不能为空！")
    c = clean_text(val)
    if c in STATUS_MAP:
        return STATUS_MAP[c]
    if default is not None:
        return default
    valid_opts = sorted(set(STATUS_MAP.values()))
    raise ValueError(f"无效的 status: '{val}'。合法选项为: {', '.join(valid_opts)}")


def normalize_task_type(val: Any, default: str | None = None) -> str:
    """将输入类型归一化为正典合法的 Literal 任务类型。非法输入且未指定 default 时抛出 ValueError。"""
    if val is None or str(val).strip() == "":
        if default is not None:
            return default
        raise ValueError("task_type 不能为空！")
    c = clean_text(val)
    if c in TYPE_MAP:
        return TYPE_MAP[c]
    if default is not None:
        return default
    valid_opts = sorted(set(TYPE_MAP.values()))
    raise ValueError(f"无效的 task_type: '{val}'。合法选项为: {', '.join(valid_opts)}")


def normalize_priority(val: Any, default: str | None = None) -> str:
    """将输入优先级归一化为 P0/P1/P2/P3。非法输入且未指定 default 时抛出 ValueError。"""
    if val is None or str(val).strip() == "":
        if default is not None:
            return default
        raise ValueError("priority 不能为空！")
    c = clean_text(val).lower()
    if c in PRIORITY_MAP:
        return PRIORITY_MAP[c]
    if default is not None:
        return default
    valid_opts = sorted(set(PRIORITY_MAP.values()))
    raise ValueError(f"无效的 priority: '{val}'。合法选项为: {', '.join(valid_opts)}")


def normalize_severity(val: Any, default: str | None = None) -> str:
    """将输入严重度归一化为 P1/P2/P3。非法输入且未指定 default 时抛出 ValueError。"""
    if val is None or str(val).strip() == "":
        if default is not None:
            return default
        raise ValueError("severity 不能为空！")
    c = clean_text(val).lower()
    if c in SEVERITY_MAP:
        return SEVERITY_MAP[c]
    if default is not None:
        return default
    valid_opts = sorted(set(SEVERITY_MAP.values()))
    raise ValueError(f"无效的 severity: '{val}'。合法选项为: {', '.join(valid_opts)}")


def normalize_waiting_category(val: Any, default: str | None = None) -> str:
    """将输入待办类别归一化为 user/external/closing。非法输入且未指定 default 时抛出 ValueError。"""
    if val is None or str(val).strip() == "":
        if default is not None:
            return default
        raise ValueError("waiting category 不能为空！")
    c = clean_text(val)
    if c in WAITING_CATEGORY_MAP:
        return WAITING_CATEGORY_MAP[c]
    if default is not None:
        return default
    valid_opts = sorted(set(WAITING_CATEGORY_MAP.values()))
    raise ValueError(f"无效的 waiting category: '{val}'。合法选项为: {', '.join(valid_opts)}")


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
    # 政策法规、智库研判与治理 (0.5.0+)
    "policy_macro": "policy_macro",
    "macro": "policy_macro",
    "policy": "policy_macro",
    "strategy": "policy_macro",
    "policy_industry": "policy_industry",
    "industry": "policy_industry",
    "market": "policy_industry",
    "policy_tech": "policy_tech",
    "tech_policy": "policy_tech",
    "data_security": "policy_tech",
    "data_governance": "policy_tech",
    "policy_fiscal": "policy_fiscal",
    "fiscal": "policy_fiscal",
    "finance": "policy_fiscal",
    "tax": "policy_fiscal",
    "governance": "governance",
    "standard": "governance",
    "compliance": "governance",
    "regulation": "governance",
    "lore": "lore",
    "general": "general",
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
    # 政策生命周期状态扩展 (0.5.0+)
    "in_force": "in_force",
    "effective": "in_force",
    "valid": "in_force",
    "draft": "draft",
    "revised": "revised",
    "amended": "revised",
    "repealed": "repealed",
    "abolished": "repealed",
}


def normalize_research_category(val: Any, default: str | None = None) -> str:
    """将输入调研分类归一化为正典合法的 ResearchCategory。非法输入且未指定 default 时抛出 ValueError。"""
    if val is None or str(val).strip() == "":
        if default is not None:
            return default
        raise ValueError("research category 不能为空！")
    c = clean_text(val)
    if c in RESEARCH_CATEGORY_MAP:
        return RESEARCH_CATEGORY_MAP[c]
    if default is not None:
        return default
    valid_opts = sorted(set(RESEARCH_CATEGORY_MAP.values()))
    raise ValueError(f"无效的 research category: '{val}'。合法选项为: {', '.join(valid_opts)}")


def normalize_research_status(val: Any, default: str | None = None) -> str:
    """将输入调研状态归一化为正典合法的 ResearchStatus。非法输入且未指定 default 时抛出 ValueError。"""
    if val is None or str(val).strip() == "":
        if default is not None:
            return default
        raise ValueError("research status 不能为空！")
    c = clean_text(val)
    if c in RESEARCH_STATUS_MAP:
        return RESEARCH_STATUS_MAP[c]
    if default is not None:
        return default
    valid_opts = sorted(set(RESEARCH_STATUS_MAP.values()))
    raise ValueError(f"无效的 research status: '{val}'。合法选项为: {', '.join(valid_opts)}")


# ============================================================================
# 数据集与事实记录归一化 (0.5.0)
# ============================================================================

DATASET_CATEGORY_MAP = {
    "finance": "finance",
    "financial": "finance",
    "oss_metrics": "oss_metrics",
    "oss": "oss_metrics",
    "benchmark": "oss_metrics",
    "trade": "trade",
    "customs": "trade",
    "tax": "tax",
    "research": "research",
    "general": "general",
}

RECORD_STATUS_MAP = {
    "recorded": "recorded",
    "record": "recorded",
    "new": "recorded",
    "reconciled": "reconciled",
    "checked": "reconciled",
    "audited": "reconciled",
    "verified": "verified",
    "valid": "verified",
    "cancelled": "cancelled",
    "canceled": "cancelled",
    "void": "cancelled",
    "active": "active",
    "planned": "planned",
    "running": "running",
    "review": "review",
    "blocked": "blocked",
    "closed": "closed",
    "wontfix": "wontfix",
    "open": "open",
    "infix": "infix",
    "fixed": "fixed",
    "completed": "completed",
    "halted": "halted",
    "archived": "archived",
    "read": "read",
    "unread": "unread",
    "delivered": "delivered",
    "project_private": "project_private",
    "org_internal": "org_internal",
    "public_safe": "public_safe",
}


def normalize_dataset_category(val: Any, default: str | None = None) -> str:
    """将输入数据集分类归一化为正典合法的 DatasetCategory。非法输入且未指定 default 时抛出 ValueError。"""
    if val is None or str(val).strip() == "":
        if default is not None:
            return default
        raise ValueError("dataset category 不能为空！")
    c = clean_text(val)
    if c in DATASET_CATEGORY_MAP:
        return DATASET_CATEGORY_MAP[c]
    if default is not None:
        return default
    valid_opts = sorted(set(DATASET_CATEGORY_MAP.values()))
    raise ValueError(f"无效的 dataset category: '{val}'。合法选项为: {', '.join(valid_opts)}")


def normalize_record_status(val: Any, default: str | None = None) -> str:
    """将输入记录状态归一化为正典合法的 RecordStatus。非法输入且未指定 default 时抛出 ValueError。"""
    if val is None or str(val).strip() == "":
        if default is not None:
            return default
        return "recorded"
    c = clean_text(val).lower()
    if c in RECORD_STATUS_MAP:
        return RECORD_STATUS_MAP[c]
    if default is not None:
        return default
    valid_opts = sorted(set(RECORD_STATUS_MAP.values()))
    raise ValueError(f"无效的 record status: '{val}'。合法选项为: {', '.join(valid_opts)}")


def extract_record_embed_text(record: Any) -> str:
    """提取单据或事实记录的关键文本用于 768 维语义向量化。"""
    if record is None:
        return ""
    if isinstance(record, dict):
        doc_no = str(record.get("doc_no") or "").strip()
        entity = str(record.get("entity_name") or "").strip()
        payload = record.get("payload") or {}
    else:
        doc_no = str(getattr(record, "doc_no", "") or "").strip()
        entity = str(getattr(record, "entity_name", "") or "").strip()
        payload = getattr(record, "payload", {}) or {}

    parts = []
    if doc_no:
        parts.append(doc_no)
    if entity:
        parts.append(entity)

    if isinstance(payload, dict):
        priority_keys = ["title", "name", "subject", "summary", "content", "body", "text", "description", "remark", "clause", "law_name"]
        found = False
        for k in priority_keys:
            v = payload.get(k)
            if v and isinstance(v, (str, int, float)):
                parts.append(str(v).strip())
                found = True
        if not found:
            for k, v in payload.items():
                if v and isinstance(v, (str, int, float)):
                    parts.append(f"{k}:{v}")
    elif isinstance(payload, str) and payload.strip():
        parts.append(payload.strip())

    full_text = " ".join(parts).strip()
    return full_text[:2048]


