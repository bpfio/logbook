"""Logbook 0.8.1 (L-OPT-081) 读写对称性与局部补丁优化专项单元测试套件。

涵盖场景:
1. record_query 支持 id (标量与数组) 精准过滤与 applied_filters 回显;
2. record_upsert patch=True 局部更新合并原有 payload，避免全量重传与静默丢字段;
3. record_upsert 指定 id 更新时支持省略 doc_no 自动沿用原单号;
4. record_upsert 新增模式若缺失 doc_no 则严格以 INVALID_ARGUMENT 阻断;
5. validate_payload_schema 契约拦截信息包含可用必填项与自愈指引。
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from decimal import Decimal

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
async def test_record_upsert_patch_mode_preserves_unmodified_fields():
    """核验 ISSUE-02: record_upsert patch=True 局部更新合并已有 payload，防范静默丢内容。"""
    existing_row = {
        "id": 1001,
        "doc_no": "DEVLOG-1001",
        "payload": {
            "title": "故障排查手记",
            "problem": "连接池超时",
            "root_cause": "慢查询阻塞连接",
            "solution": "临时放大连接池",
            "evidence": "单测通过",
        },
        "entity_name": "db_module",
    }

    updated_row = {
        "id": 1001,
        "project_id": "logbook",
        "dataset_code": "sys_devlog",
        "parent_id": None,
        "batch_id": None,
        "doc_no": "DEVLOG-1001",
        "entity_name": "db_module",
        "amount": Decimal("0.0"),
        "currency": "CNY",
        "event_date": "2026-11-14",
        "status": "recorded",
        "payload": {
            "title": "故障排查手记",
            "problem": "连接池超时",
            "root_cause": "慢查询阻塞连接",
            "solution": "重构索引并缩减锁粒度 (已局部修正)",
            "evidence": "单测通过",
        },
        "created_at": "2026-11-14T10:00:00",
        "updated_at": "2026-11-14T12:00:00",
    }

    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()
    # 模拟 existing_row 查询命中
    mock_conn.fetchrow = AsyncMock(side_effect=[existing_row, updated_row])

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn
    mock_pool.acquire.return_value.__aexit__.return_value = None

    with patch.object(db, "_pool", mock_pool), \
         patch.object(db, "ensure_datasets_and_records_schema", AsyncMock()), \
         patch("logbook.mcp_server.validate_and_negotiate_project", AsyncMock(return_value="logbook")), \
         patch.object(fact_engine, "validate_payload_schema", AsyncMock()) as mock_validate:

        # 仅传递需要局部修改的 solution 字段，开启 patch=True
        res = await record_upsert(
            project="logbook",
            dataset_code="sys_devlog",
            id=1001,
            patch=True,
            payload={"solution": "重构索引并缩减锁粒度 (已局部修正)"},
        )

        assert res.get("isError") is not True
        assert res["success"] is True
        assert res["record"]["payload"]["solution"] == "重构索引并缩减锁粒度 (已局部修正)"
        assert res["record"]["payload"]["problem"] == "连接池超时"
        
        # 验证合并后的完整 payload 参与了 schema 契约校验
        mock_validate.assert_called_once()
        validated_code, validated_payload = mock_validate.call_args.args
        assert validated_code == "sys_devlog"
        assert validated_payload["title"] == "故障排查手记"
        assert validated_payload["problem"] == "连接池超时"
        assert validated_payload["solution"] == "重构索引并缩减锁粒度 (已局部修正)"


@pytest.mark.asyncio
async def test_record_upsert_with_id_omitting_doc_no():
    """核验 ISSUE-03: 传递物理主键 id 更新时支持省略 doc_no 自动保留原单号。"""
    existing_row = {
        "id": 8888,
        "doc_no": "ORIGINAL-DOC-88",
        "payload": {"title": "原单据"},
        "entity_name": "test_entity",
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
        assert res["record"]["doc_no"] == "ORIGINAL-DOC-88"


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
