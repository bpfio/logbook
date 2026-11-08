"""Logbook 0.8.0 回归与缺陷修复专项单元测试套件 (F-API-08, F-SEARCH-07, F-API-09, F-API-03, F-SEARCH-08)"""

import pytest
from unittest.mock import AsyncMock, patch
from logbook.mcp_server import (
    record_query,
    record_search,
    project_fields,
    VALID_RECORD_FIELDS,
)
from logbook.errors import ToolError
from logbook.models import DataRecord
from logbook.db import db


@pytest.mark.asyncio
async def test_record_query_status_scalar_and_list():
    """F-API-08 / F-API-09: record_query 支持标量 status='open' 及多状态列表。"""
    mock_r = DataRecord(
        project_id="taxbase",
        dataset_code="sys_waiting",
        doc_no="W-01",
        entity_name="待办事项",
        status="open",
    )
    with patch("logbook.mcp_server.validate_and_negotiate_project", AsyncMock(return_value="taxbase")), \
         patch.object(db, "query_records", AsyncMock(return_value=[mock_r])) as mock_q:
        # 1. 标量 status
        res1 = await record_query(project="taxbase", dataset_code="sys_waiting", status="open")
        assert res1.get("isError") is not True
        assert res1["success"] is True

        # 2. 列表 status
        res2 = await record_query(project="taxbase", dataset_code="sys_waiting", status=["open", "closed"])
        assert res2.get("isError") is not True
        assert res2["success"] is True


@pytest.mark.asyncio
async def test_record_search_empty_query_rejected():
    """F-SEARCH-07: record_search 遇到空串或纯空格，严格阻断并返回 INVALID_ARGUMENT 信封。"""
    with patch("logbook.mcp_server.validate_and_negotiate_project", AsyncMock(return_value="taxbase")):
        res_empty = await record_search(project="taxbase", query="")
        assert res_empty.get("isError") is True
        assert res_empty.get("error_type") == "INVALID_ARGUMENT"
        assert "不能为空" in res_empty.get("detail", "")

        res_spaces = await record_search(project="taxbase", query="   ")
        assert res_spaces.get("isError") is True
        assert res_spaces.get("error_type") == "INVALID_ARGUMENT"


@pytest.mark.asyncio
async def test_project_fields_unknown_field_rejected():
    """F-API-03: project_fields 遇到未知投影列抛出 INVALID_ARGUMENT 异常，阻止幽灵列投影。"""
    data = [{"id": 1, "doc_no": "A001", "status": "recorded"}]
    # 合法字段
    ok = project_fields(data, fields=["doc_no", "status"], valid_keys=VALID_RECORD_FIELDS)
    assert ok == [{"doc_no": "A001", "status": "recorded"}]

    # 非法未知字段
    with pytest.raises(ToolError) as exc_info:
        project_fields(data, fields=["doc_no", "ghost_col"], valid_keys=VALID_RECORD_FIELDS)
    assert exc_info.value.error_type == "INVALID_ARGUMENT"
    assert "未知投影字段" in exc_info.value.detail


@pytest.mark.asyncio
async def test_record_query_echoes_fields_and_validates():
    """F-API-03: record_query 在指定 fields 时回显至 applied_filters，并在未知 fields 时报错。"""
    with patch("logbook.mcp_server.validate_and_negotiate_project", AsyncMock(return_value="taxbase")), \
         patch.object(db, "query_records", AsyncMock(return_value=[{"id": 1, "doc_no": "A1"}])):
        # 1. 合法 fields
        res = await record_query(project="taxbase", fields=["id", "doc_no"])
        assert res.get("isError") is not True
        assert res["applied_filters"]["fields"] == ["id", "doc_no"]
        assert res["items"] == [{"id": 1, "doc_no": "A1"}]

        # 2. 未知 fields
        res_err = await record_query(project="taxbase", fields=["bogus_field"])
        assert res_err.get("isError") is True
        assert res_err.get("error_type") == "INVALID_ARGUMENT"
        assert "未知投影字段" in res_err.get("detail", "")
