"""Logbook 统一结构化错误面 (P0-2)

目标: 根治 MCP SDK "Error executing tool X" 零细节裸包装。
统一形态: {isError: True, error_type, detail, ...kw}
- PG 异常映射约束名/诊断 detail (唯一键/外键/check/非空)
- negotiation 裸 ValueError / PermissionError 收编为结构化形态
"""

from __future__ import annotations

import asyncpg


def tool_error(error_type: str, detail: str, **kw) -> dict:
    """构造统一结构化错误响应 dict。"""
    payload = {"isError": True, "error_type": error_type, "detail": detail}
    payload.update(kw)
    return payload


class ToolError(Exception):
    """携带结构化形态的异常基类; 由工具体外壳捕获转 tool_error dict。"""

    def __init__(self, error_type: str, detail: str, **kw):
        super().__init__(detail)
        self.error_type = error_type
        self.detail = detail
        self.extra = kw

    def to_dict(self) -> dict:
        return tool_error(self.error_type, self.detail, **self.extra)


class NegotiationValueError(ToolError, ValueError):
    """语法级参数错误 (如 project 为空), 兼容既有 ValueError 捕获面。"""

    def __init__(self, detail: str, **kw):
        super().__init__("INVALID_ARGUMENT", detail, **kw)


class NegotiationPermissionError(ToolError, PermissionError):
    """越权/隔离拦截, 兼容既有 PermissionError 捕获面。"""

    def __init__(self, detail: str, **kw):
        super().__init__("CROSS_PROJECT_FORBIDDEN", detail, **kw)


# asyncpg 错误码 -> error_type 映射 (PG Class 23 约束违反)
_PG_CONSTRAINT_MAP = {
    "23502": "NOT_NULL_VIOLATION",
    "23503": "FK_VIOLATION",
    "23505": "DUPLICATE_KEY",
    "23514": "CHECK_VIOLATION",
}


def pg_tool_error(exc: Exception, tool: str | None = None) -> dict:
    """将 asyncpg 异常映射为结构化错误 dict (含约束名/诊断 detail)。"""
    if isinstance(exc, asyncpg.PostgresError):
        kw: dict = {"pg_code": getattr(exc, "sqlstate", None) or getattr(exc, "code", None)}
        constraint = getattr(exc, "constraint_name", None)
        if constraint:
            kw["constraint"] = constraint
        diag = getattr(exc, "detail", None)
        if diag:
            kw["pg_detail"] = diag
        error_type = _PG_CONSTRAINT_MAP.get(str(kw["pg_code"]), "DB_ERROR")
        prefix = f"[{tool}] " if tool else ""
        return tool_error(error_type, f"{prefix}{getattr(exc, 'message', None) or str(exc)}", **kw)
    return tool_error("DB_ERROR", f"[{tool}] {exc}" if tool else str(exc))
