"""Logbook 通用事实双表正典业务引擎 (Fact Engine)

系统单一事实源 (SSOT):
1. shared.datasets: 数据集与字段规范元数据字典
2. shared.records: 工业级通用事实存储底座 (强脊梁 + 软肋骨 JSONB + 768维原生向量)

本引擎统一收敛：
- 8 大系统级业务实体 (sys_task, sys_finding, sys_waiting, sys_batch, sys_devlog, sys_message, sys_research, sys_rule)
- 业务扩展数据集 (如税收法规、财务凭单、知识库文章等)
- 参数归一化、边界防护、防幽灵批次校验 (F-API-13)、空串防御 (F-SEARCH-07)
- 全文倒排与星火向量语义混合检索
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, date
from decimal import Decimal
from typing import Any, Literal

from .db import Database, db, MAX_LIMIT
from .models import DataRecord, RecordStatus
from .sanitizer import sanitize_text
from .time_sync import get_beijing_now

logger = logging.getLogger(__name__)

# 系统正典数据集代码映射
SYSTEM_DATASETS = {
    "task": "sys_task",
    "finding": "sys_finding",
    "waiting": "sys_waiting",
    "batch": "sys_batch",
    "devlog": "sys_devlog",
    "message": "sys_message",
    "research": "sys_research",
    "rule": "sys_rule",
}


class FactEngine:
    def __init__(self, database: Database):
        self.db = database
        self._schema_cache: dict[str, dict[str, Any] | None] = {}

    def invalidate_schema_cache(self, dataset_code: str | None = None) -> None:
        """清空指定数据集或全量 Schema 内存缓存 (OPT-02)。"""
        if not hasattr(self, "_schema_cache"):
            self._schema_cache = {}
        if dataset_code:
            self._schema_cache.pop(dataset_code, None)
        else:
            self._schema_cache.clear()

    async def validate_payload_schema(self, dataset_code: str, payload: dict[str, Any]) -> None:
        """声明式校验 payload 是否符合该数据集在 shared.datasets 中的 json_schema 契约规范。"""
        if not dataset_code or not isinstance(payload, dict):
            return
        
        if not hasattr(self, "_schema_cache"):
            self._schema_cache: dict[str, dict[str, Any] | None] = {}
        
        schema = self._schema_cache.get(dataset_code)
        if schema is None and dataset_code not in self._schema_cache:
            try:
                await self.db.connect()
                async with self.db._pool.acquire() as conn:
                    val = await conn.fetchval(
                        "SELECT json_schema FROM shared.datasets WHERE code = $1 LIMIT 1;",
                        dataset_code
                    )
                    if val:
                        schema = val if isinstance(val, dict) else json.loads(val)
                # 仅在数据库成功查询后写入缓存 (OPT-30: 消除负向缓存污染)
                self._schema_cache[dataset_code] = schema
            except Exception as e:
                logger.warning("Failed to query json_schema for dataset %s: %s", dataset_code, e)
                schema = None
        
        if not schema:
            return

        # 校验必填字段
        required = schema.get("required", [])
        for field in required:
            val = payload.get(field)
            if val is None or (isinstance(val, str) and not val.strip()):
                raise ValueError(f"数据集 [{dataset_code}] 契约校验失败: 必填字段 '{field}' 缺失或为空！")
        
        # 校验属性类型与最小长度及枚举约束 (OPT-02)
        props = schema.get("properties", {})

        # OPT-34: 严防未知字段噪音 (additionalProperties: false)
        if schema.get("additionalProperties") is False:
            allowed_fields = set(props.keys())
            extra_keys = set(payload.keys()) - allowed_fields
            if extra_keys:
                extra_str = ", ".join(sorted(extra_keys))
                raise ValueError(f"数据集 [{dataset_code}] 契约校验失败: 存在未定义的非法多余字段: [{extra_str}]！")
        for field, spec in props.items():
            if field in payload and payload[field] is not None:
                val = payload[field]
                p_type = spec.get("type")
                if p_type == "string":
                    if not isinstance(val, str):
                        raise ValueError(f"数据集 [{dataset_code}] 契约校验失败: 字段 '{field}' 必须为字符串类型！")
                    min_len = spec.get("minLength")
                    if min_len and len(val.strip()) < min_len:
                        raise ValueError(f"数据集 [{dataset_code}] 契约校验失败: 字段 '{field}' 长度不能小于 {min_len}！")
                elif p_type == "number":
                    if isinstance(val, bool) or not isinstance(val, (int, float, Decimal)):
                        raise ValueError(f"数据集 [{dataset_code}] 契约校验失败: 字段 '{field}' 必须为数值类型！")
                elif p_type == "integer":
                    if isinstance(val, bool) or not isinstance(val, int) or isinstance(val, float):
                        raise ValueError(f"数据集 [{dataset_code}] 契约校验失败: 字段 '{field}' 必须为整数类型！")
                elif p_type == "boolean" and not isinstance(val, bool):
                    raise ValueError(f"数据集 [{dataset_code}] 契约校验失败: 字段 '{field}' 必须为布尔类型 (true/false)！")
                elif p_type == "array" and not isinstance(val, (list, tuple)):
                    raise ValueError(f"数据集 [{dataset_code}] 契约校验失败: 字段 '{field}' 必须为数组类型！")
                elif p_type == "object" and not isinstance(val, dict):
                    raise ValueError(f"数据集 [{dataset_code}] 契约校验失败: 字段 '{field}' 必须为字典对象！")

                enum_vals = spec.get("enum")
                if enum_vals is not None and val not in enum_vals:
                    raise ValueError(f"数据集 [{dataset_code}] 契约校验失败: 字段 '{field}' 的值 '{val}' 不在合法枚举范围 {enum_vals} 内！")

    # -------------------------------------------------------------------------
    # 参数归一化与校验原语 (消除 F-API-10, F-SEARCH-07 等入参口径漂移)
    # -------------------------------------------------------------------------

    @staticmethod
    def clamp_limit(limit: int | None, default: int = 50, max_val: int = MAX_LIMIT) -> int:
        """统一钳制 LIMIT 参数 [1, max_val]。"""
        try:
            n = int(limit) if limit is not None else default
        except (TypeError, ValueError):
            return default
        if n < 1:
            return default
        return min(n, max_val)

    @staticmethod
    def normalize_status_list(status: Any) -> list[str] | None:
        """统一将 status 参数 (字符串标量或数组) 归一化为纯净的小写字符串列表。"""
        if status is None:
            return None
        if isinstance(status, (list, tuple, set)):
            return [str(s).strip().lower() for s in status if s is not None and str(s).strip()]
        s = str(status).strip().lower()
        return [s] if s else []

    @staticmethod
    def sanitize_field(val: Any) -> str:
        """脱敏并清洗字符串字段。"""
        if val is None:
            return ""
        return sanitize_text(str(val)).clean_text.strip()


# 全局事实引擎单例接入点 (直连既有全局 db 实例)
fact_engine = FactEngine(db)
