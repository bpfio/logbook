"""Logbook 0.8.1 (L-OPT-081) 读写对称性、局部补丁模式 (F-1/F-2) 与严格 ID 防御 (F-3/F-4) 专项单元测试套件。

涵盖场景:
1. record_query 支持 id (标量与数组) 精准过滤与 applied_filters 回显;
2. (F-3/F-4) record_query 对非法 id ("abc", 0, 负数, '²', 布尔型, 空列表, 混合非法项) 严格以 INVALID_ARGUMENT 阻断，绝不 fail-open;
3. (F-1/F-2) record_upsert patch=True 局部更新合并 payload，并严格保全强脊梁 (status, amount, currency, event_date, entity_name, batch_id, parent_id) 原值，通过断言真实 SQL 绑定参数确认;
4. record_upsert 指定 id 更新时支持省略 doc_no 自动沿用原单号;
5. record_upsert 新增模式若缺失 doc_no 则严格以 INVALID_ARGUMENT 阻断;
6. validate_payload_schema 契约拦截信息包含可用必填项与自愈指引。
"""

import json
from datetime import date
from decimal import Decimal
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from logbook.mcp_server import record_query, record_upsert
from logbook.models import DataRecord
from logbook.db import db
from logbook.engine import fact_engine


@pytest.mark.asyncio
async def test_record_query_supports_id_scalar_and_list():
    """核验 ISSUE-01: record_query 读端支持 id 标量与数组，消除 extra_forbidden。"""
    mock_item = {
        "id": 9773,
        "project_id": "test_proj",
        "dataset_code": "sys_task",
        "doc_no": "TASK-001",
        "payload": {"title": "Test Task"},
    }
    with patch("logbook.mcp_server.validate_and_negotiate_project", AsyncMock(return_value="test_proj")), \
         patch.object(db, "query_records", AsyncMock(return_value=[mock_item])) as mock_q:
        
        # 1. 标量 ID
        res_scalar = await record_query(project="test_proj", id=9773)
        assert res_scalar.get("isError") is not True
        assert res_scalar["success"] is True
        assert res_scalar["applied_filters"]["id"] == 9773
        assert len(res_scalar["items"]) == 1
        assert res_scalar["items"][0]["id"] == 9773
        mock_q.assert_called_with(
            project_id="test_proj",
            dataset_code=None,
            doc_no=None,
            entity_name=None,
            date_from=None,
            date_to=None,
            amount_min=None,
            amount_max=None,
            status=None,
            batch_id=None,
            parent_id=None,
            payload_filter=None,
            query_text=None,
            limit=50,
            offset=0,
            id=9773,
        )

        # 2. 数组 ID
        res_list = await record_query(project="test_proj", id=[9773, 9774])
        assert res_list.get("isError") is not True
        assert res_list["applied_filters"]["id"] == [9773, 9774]
        assert mock_q.call_args.kwargs["id"] == [9773, 9774]


@pytest.mark.asyncio
async def test_record_query_rejects_invalid_id_fail_closed():
    """核验 F-3 / F-4: record_query 对非法 id 严格阻断报错，绝不 fail-open 穿透全表。"""
    with patch("logbook.mcp_server.validate_and_negotiate_project", AsyncMock(return_value="test_proj")):
        # 1. 纯字母字符串
        res_str = await record_query(project="test_proj", id="abc")
        assert res_str.get("isError") is True
        assert res_str.get("error_type") == "INVALID_ARGUMENT"
        assert "id 必须为正整数" in res_str.get("detail", "")

        # 2. 0
        res_zero = await record_query(project="test_proj", id=0)
        assert res_zero.get("isError") is True
        assert res_zero.get("error_type") == "INVALID_ARGUMENT"
        assert "id 必须为正整数" in res_zero.get("detail", "")

        # 3. 负数
        res_neg = await record_query(project="test_proj", id=-5)
        assert res_neg.get("isError") is True
        assert res_neg.get("error_type") == "INVALID_ARGUMENT"
        assert "id 必须为正整数" in res_neg.get("detail", "")

        # 4. Unicode 上标数字 (防 isdigit 误判漏洞)
        res_unicode = await record_query(project="test_proj", id="²")
        assert res_unicode.get("isError") is True
        assert res_unicode.get("error_type") == "INVALID_ARGUMENT"
        assert "id 必须为正整数" in res_unicode.get("detail", "")

        # 5. 布尔型 (bool 是 int 子类，必须显式拦截)
        res_bool = await record_query(project="test_proj", id=True)
        assert res_bool.get("isError") is True
        assert res_bool.get("error_type") == "INVALID_ARGUMENT"
        assert "布尔类型" in res_bool.get("detail", "")

        # 6. 空数组
        res_empty = await record_query(project="test_proj", id=[])
        assert res_empty.get("isError") is True
        assert res_empty.get("error_type") == "INVALID_ARGUMENT"
        assert "id 列表不能为空" in res_empty.get("detail", "")

        # 7. 数组中含非法项
        res_mixed = await record_query(project="test_proj", id=[9773, "bad_id"])
        assert res_mixed.get("isError") is True
        assert res_mixed.get("error_type") == "INVALID_ARGUMENT"
        assert "id 必须为正整数" in res_mixed.get("detail", "")


@pytest.mark.asyncio
async def test_record_upsert_patch_preserves_spine_and_ribs_assert_sql_args():
    """核验 F-1 / F-2: record_upsert patch=True 必须保全强脊梁 (状态/日期/金额/币种/单位) 与软肋骨，直接断言真实 SQL 参数。"""
    existing_row = {
        "id": 1001,
        "doc_no": "TASK-1001",
        "entity_name": "core_engine",
        "amount": Decimal("128.5000"),
        "currency": "USD",
        "event_date": date(2026, 10, 1),
        "status": "closed",  # 核心生命周期状态为 closed
        "batch_id": "BATCH-DEV-01",
        "parent_id": 500,
        "payload": {
            "task_id": "T1001",
            "title": "生产任务",
            "notes": "初始笔记",
            "reviewer": "chief_arch",
        },
    }

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    # 第一次 fetchrow 返回 existing_row，第二次返回 mock 执行结果
    mock_conn.fetchrow = AsyncMock(side_effect=[existing_row, {"id": 1001, "status": "closed"}])

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_pool.acquire.return_value.__aexit__.return_value = None

    with patch.object(db, "_pool", mock_pool), \
         patch.object(db, "ensure_datasets_and_records_schema", AsyncMock()), \
         patch("logbook.mcp_server.validate_and_negotiate_project", AsyncMock(return_value="logbook")), \
         patch.object(fact_engine, "validate_payload_schema", AsyncMock()) as mock_validate:

        # 核心场景: 仅通过 patch=True 修改 notes 属性，不传递任何脊柱字段
        res = await record_upsert(
            project="logbook",
            dataset_code="sys_task",
            id=1001,
            patch=True,
            payload={"notes": "更新后的复盘笔记 (局部修改)"},
        )

        assert res.get("isError") is not True
        assert res["success"] is True

        # F-2 关键断言: 绝不读写死在 updated_row 里的台词，直接检查传入 conn.fetchrow 的 update_sql 真实绑定参数
        assert mock_conn.fetchrow.call_count == 2
        update_call = mock_conn.fetchrow.call_args_list[1]
        sql_query = update_call.args[0]
        sql_params = update_call.args[1:]

        # 参数映射对应 update_sql:
        # $1: existing_id (1001)
        # $2: dataset_code ('sys_task')
        # $3: parent_id (500)
        # $4: batch_id ('BATCH-DEV-01')
        # $5: doc_no ('TASK-1001')
        # $6: entity_name ('core_engine')
        # $7: amount (Decimal('128.5000'))
        # $8: currency ('USD')
        # $9: event_date (date(2026, 10, 1))
        # $10: status ('closed' - 绝对不能被刷成 recorded!)
        # $11: payload (json string)
        # $13: project_id ('logbook')
        assert sql_params[0] == 1001
        assert sql_params[1] == "sys_task"
        assert sql_params[2] == 500, "parent_id 必须保全原值"
        assert sql_params[3] == "BATCH-DEV-01", "batch_id 必须保全原值"
        assert sql_params[4] == "TASK-1001", "doc_no 必须保全原值"
        assert sql_params[5] == "core_engine", "entity_name 必须保全原值"
        assert sql_params[6] == Decimal("128.5000"), "amount 必须保全原值，绝对不能被重置为 0"
        assert sql_params[7] == "USD", "currency 必须保全原值，绝对不能被重置为 CNY"
        assert sql_params[8] == date(2026, 10, 1), "event_date 必须保全原业务日期，绝对不能被重置为今天"
        assert sql_params[9] == "closed", "status 必须保全 closed，绝对不能被重置为 recorded！"

        # 验证 payload 合并：既有 reviewer 键保留，notes 键被更新
        merged_payload = json.loads(sql_params[10])
        assert merged_payload["reviewer"] == "chief_arch"
        assert merged_payload["task_id"] == "T1001"
        assert merged_payload["notes"] == "更新后的复盘笔记 (局部修改)"

        # 验证 Schema 校验以完整合并后的 payload 执行
        mock_validate.assert_called_once()
        v_code, v_payload = mock_validate.call_args.args
        assert v_code == "sys_task"
        assert v_payload["notes"] == "更新后的复盘笔记 (局部修改)"
        assert v_payload["task_id"] == "T1001"


@pytest.mark.asyncio
async def test_record_upsert_with_id_omitting_doc_no():
    """核验 ISSUE-03: 传递物理主键 id 更新时支持省略 doc_no 自动保留原单号。"""
    existing_row = {
        "id": 8888,
        "doc_no": "ORIGINAL-DOC-88",
        "entity_name": "test_entity",
        "amount": Decimal("0.0"),
        "currency": "CNY",
        "event_date": date(2026, 11, 14),
        "status": "planned",
        "batch_id": None,
        "parent_id": None,
        "payload": {"title": "原单据"},
    }
    updated_row = {
        "id": 8888,
        "project_id": "test_proj",
        "dataset_code": "sys_task",
        "parent_id": None,
        "batch_id": None,
        "doc_no": "ORIGINAL-DOC-88",
        "entity_name": "test_entity",
        "amount": Decimal("0.0"),
        "currency": "CNY",
        "event_date": "2026-11-14",
        "status": "closed",
        "payload": {"task_id": "T88", "title": "已办结任务"},
        "created_at": "2026-11-14T10:00:00",
        "updated_at": "2026-11-14T12:00:00",
    }

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    mock_conn.fetchrow = AsyncMock(side_effect=[existing_row, updated_row])

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_pool.acquire.return_value.__aexit__.return_value = None

    with patch.object(db, "_pool", mock_pool), \
         patch.object(db, "ensure_datasets_and_records_schema", AsyncMock()), \
         patch("logbook.mcp_server.validate_and_negotiate_project", AsyncMock(return_value="test_proj")), \
         patch.object(fact_engine, "validate_payload_schema", AsyncMock()):

        # 省略 doc_no，仅传 id 更新状态与载荷
        res = await record_upsert(
            project="test_proj",
            dataset_code="sys_task",
            id=8888,
            status="closed",
            payload={"task_id": "T88", "title": "已办结任务"},
        )

        assert res.get("isError") is not True
        assert res["success"] is True
        # 验证 SQL 参数实际传入了原有 doc_no
        sql_params = mock_conn.fetchrow.call_args_list[1].args[1:]
        assert sql_params[4] == "ORIGINAL-DOC-88"


@pytest.mark.asyncio
async def test_record_upsert_insert_without_doc_no_rejected():
    """核验 ISSUE-03 防呆: 新增单据 (id is None) 且省略 doc_no 必须被拦截阻断。"""
    with patch("logbook.mcp_server.validate_and_negotiate_project", AsyncMock(return_value="test_proj")):
        res = await record_upsert(
            project="test_proj",
            dataset_code="sys_task",
            doc_no="",  # 空单号
            payload={"task_id": "T01", "title": "新建任务"},
        )
        assert res.get("isError") is True
        assert res.get("error_type") == "INVALID_ARGUMENT"
        assert "新增事实单据必须提供 doc_no 单据号" in res.get("detail", "")


@pytest.mark.asyncio
async def test_validate_payload_schema_error_guidance():
    """核验 ISSUE-04: Schema 契约校验失败信息中提供必填项清单与查询自愈指引。"""
    schema = {
        "type": "object",
        "required": ["title", "problem", "root_cause", "solution", "evidence"],
        "properties": {
            "title": {"type": "string"},
            "problem": {"type": "string"},
            "root_cause": {"type": "string"},
            "solution": {"type": "string"},
            "evidence": {"type": "string"},
        }
    }
    fact_engine._schema_cache["sys_devlog"] = schema

    with pytest.raises(ValueError) as exc_info:
        # 故意缺失 solution 和 evidence
        await fact_engine.validate_payload_schema(
            "sys_devlog",
            {"title": "手记标题", "problem": "故障", "root_cause": "原因"}
        )

    err_msg = str(exc_info.value)
    assert "必填字段 'solution' 缺失或为空" in err_msg
    assert "规范定义必填项" in err_msg
    assert "dataset_manage" in err_msg
    assert "logbook://schema/fields" in err_msg
