"""Logbook 枚举与入参轻量级归一化处理层 (Normalizer) - 0.8.0 极简正典

功能:
- 剥离 Emoji 字符与富文本格式干扰
- 数据集分类 (DatasetCategory) 与事实记录状态 (RecordStatus) 归一化
- 单据/事实记录语义向量嵌入关键文本抽取
"""

import re
from typing import Any

# Emoji 与常见 Markdown 符号正则
EMOJI_PATTERN = re.compile(
    r"[\U00010000-\U0010ffff]|[\u2600-\u27bf]|[\u2300-\u23ff]|[\u2b50-\u2b55]|[\u3030\u303d]|[\ufe0f\u200d]|[\u2022\u25aa\u25ab\u25cf\u25cb]",
    flags=re.UNICODE,
)


def clean_text(val: Any) -> str:
    """去除首尾空白及 Markdown/Emoji 装饰符号。"""
    if val is None:
        return ""
    text = str(val).strip()
    text = EMOJI_PATTERN.sub("", text).strip()
    # 去除两端可能残留的方括号/圆括号
    text = text.strip("[]() ")
    return text.lower()


# ============================================================================
# 数据集与事实记录归一化 (0.5.0 / 0.8.0)
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
