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
            except Exception as e:
                logger.warning("Failed to query json_schema for dataset %s: %s", dataset_code, e)
                schema = None
            self._schema_cache[dataset_code] = schema
        
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

    # -------------------------------------------------------------------------
    # 引用完整性与防幽灵校验 (F-API-11, F-API-13: 彻底杜绝幽灵批次自动凭空创建)
    # -------------------------------------------------------------------------

    async def ensure_batch_exists_or_raise(self, project_id: str, batch_id: str | None) -> None:
        """校验批次号在当前项目中是否存在，若不存在则抛出可读的 ValueError。"""
        if not batch_id or not str(batch_id).strip():
            return
        b_id = str(batch_id).strip()
        await self.db.connect()
        async with self.db._pool.acquire() as conn:
            exists = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM shared.records
                    WHERE project_id = $1 AND dataset_code = 'sys_batch' AND doc_no = $2
                );
                """,
                project_id, b_id
            )
            if not exists:
                raise ValueError(
                    f"批次 [{b_id}] 在项目 [{project_id}] 的批次表中不存在！必须先通过 batch_upsert 建立批次主档后方可关联单据"
                )

    async def ensure_parent_task_exists_or_raise(self, project_id: str, parent_id: str | None) -> None:
        """校验父任务在当前项目中是否存在，若不存在则抛出可读的 ValueError。"""
        if not parent_id or not str(parent_id).strip():
            return
        p_id = str(parent_id).strip()
        await self.db.connect()
        async with self.db._pool.acquire() as conn:
            exists = await conn.fetchval(
                """
                SELECT EXISTS (
                    SELECT 1 FROM shared.records
                    WHERE project_id = $1 AND dataset_code = 'sys_task' AND doc_no = $2
                );
                """,
                project_id, p_id
            )
            if not exists:
                raise ValueError(
                    f"父任务 [{p_id}] 在项目 [{project_id}] 中不存在！请检查 parent_id 是否正确"
                )

    # -------------------------------------------------------------------------
    # 通用事实 CRUD 底座
    # -------------------------------------------------------------------------

    async def upsert_fact(
        self,
        project_id: str,
        dataset_code: str,
        doc_no: str,
        entity_name: str,
        payload: dict[str, Any],
        status: str = "active",
        event_date: date | str | None = None,
        parent_id: int | None = None,
        batch_id: str | None = None,
        amount: Decimal | float | None = None,
        currency: str | None = None,
        embedding: list[float] | None = None,
    ) -> dict[str, Any]:
        """原子插入或更新通用事实单据 (强脊梁 + 软肋骨)。"""
        await self.db.ensure_datasets_and_records_schema()
        
        # 边界与类型清洗
        e_name = str(entity_name).strip()[:255] if entity_name else doc_no
        st = str(status).strip().lower() if status else "active"
        
        # 契约强校验 (声明式校验 payload 结构)
        await self.validate_payload_schema(dataset_code, payload)

        # 组织 DataRecord 模型
        record = DataRecord(
            project_id=project_id,
            dataset_code=dataset_code,
            doc_no=str(doc_no).strip(),
            entity_name=e_name,
            payload=payload,
            status=st,
            event_date=event_date or date.today(),
            parent_id=parent_id,
            batch_id=batch_id,
            amount=Decimal(str(amount)) if amount is not None else None,
            currency=currency,
            embedding=embedding,
        )
        return await self.db.upsert_record(record)

    async def query_facts(
        self,
        project_id: str,
        dataset_code: str | None = None,
        doc_no: str | None = None,
        entity_name: str | None = None,
        status: list[str] | str | None = None,
        batch_id: str | None = None,
        parent_id: int | None = None,
        payload_filter: dict | None = None,
        query_text: str | None = None,
        date_from: date | str | None = None,
        date_to: date | str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """多维查询通用事实单据列表。"""
        await self.db.ensure_datasets_and_records_schema()
        lim = self.clamp_limit(limit)
        off = max(0, int(offset or 0))

        conditions = ["project_id = $1"]
        params: list[Any] = [project_id]

        if dataset_code:
            params.append(dataset_code.strip().lower())
            conditions.append(f"dataset_code = ${len(params)}")

        if doc_no:
            params.append(doc_no.strip())
            conditions.append(f"doc_no = ${len(params)}")

        if entity_name:
            params.append(f"%{entity_name.strip()}%")
            conditions.append(f"entity_name ILIKE ${len(params)}")

        st_list = self.normalize_status_list(status)
        if st_list:
            if len(st_list) == 1:
                params.append(st_list[0])
                conditions.append(f"status = ${len(params)}")
            else:
                params.append(st_list)
                conditions.append(f"status = ANY(${len(params)})")

        if batch_id:
            params.append(batch_id.strip())
            conditions.append(f"(batch_id = ${len(params)} OR payload->>'batch_id' = ${len(params)})")

        if parent_id is not None:
            params.append(int(parent_id))
            conditions.append(f"parent_id = ${len(params)}")

        if payload_filter:
            params.append(json.dumps(payload_filter, ensure_ascii=False))
            conditions.append(f"payload @> ${len(params)}::jsonb")

        if date_from:
            d_from = date_from if isinstance(date_from, date) else date.fromisoformat(str(date_from).strip()[:10])
            params.append(d_from)
            conditions.append(f"event_date >= ${len(params)}")

        if date_to:
            d_to = date_to if isinstance(date_to, date) else date.fromisoformat(str(date_to).strip()[:10])
            params.append(d_to)
            conditions.append(f"event_date <= ${len(params)}")

        where_clause = " AND ".join(conditions)

        if query_text and query_text.strip():
            params.append(query_text.strip())
            q_idx = len(params)
            where_clause += f" AND tsv_content @@ plainto_tsquery('simple', ${q_idx})"
            order_clause = f"ORDER BY ts_rank_cd(tsv_content, plainto_tsquery('simple', ${q_idx})) DESC, event_date DESC, id DESC"
        else:
            order_clause = "ORDER BY event_date DESC, id DESC"

        params.append(lim)
        lim_idx = len(params)
        params.append(off)
        off_idx = len(params)

        sql = f"""
            SELECT id, project_id, dataset_code, parent_id, batch_id,
                   doc_no, entity_name, amount, currency, event_date,
                   status, payload, created_at, updated_at
            FROM shared.records
            WHERE {where_clause}
            {order_clause}
            LIMIT ${lim_idx} OFFSET ${off_idx};
        """
        async with self.db._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            results = []
            for r in rows:
                d = dict(r)
                if isinstance(d.get("payload"), str):
                    d["payload"] = json.loads(d["payload"])
                results.append(d)
            return results

    async def search_facts(
        self,
        project_id: str,
        query_text: str,
        dataset_code: str | None = None,
        query_vector: list[float] | None = None,
        min_score: float = 0.4,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """语义向量与全文倒排混合检索 (严格空串校验 + 门限防穿透)。"""
        if not query_text or not query_text.strip():
            raise ValueError("query 不能为空！")

        return await self.db.search_records(
            project_id=project_id,
            query_vector=query_vector,
            query_text=query_text.strip(),
            dataset_code=dataset_code,
            min_score=min_score,
            limit=limit,
        )


# 全局事实引擎单例接入点 (直连既有全局 db 实例)
fact_engine = FactEngine(db)
