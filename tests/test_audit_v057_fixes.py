"""Logbook 0.5.7 回归与缺陷修复专项单元测试套件 (F-API-08, F-SEARCH-07, F-API-09, F-API-03, F-SEARCH-08)"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from logbook.mcp_server import (
    task_query,
    finding_query,
    waiting_query,
    record_query,
    record_search,
    project_fields,
    VALID_TASK_FIELDS,
    VALID_FINDING_FIELDS,
    VALID_WAITING_FIELDS,
    VALID_RECORD_FIELDS,
)
from logbook.errors import ToolError
from logbook.models import Task, Finding, Waiting, TaskStatus, TaskPriority, FindingStatus, FindingSeverity, WaitingStatus
from logbook.db import db


@pytest.mark.asyncio
async def test_waiting_query_default_clean_text():
    """F-API-08: waiting_query 默认调用 (status='open') 正常执行，无 clean_text NameError。"""
    mock_w = Waiting(
        id="W-01",
        description="待办事项",
        status=WaitingStatus.OPEN,
    )
    with patch("logbook.mcp_server.validate_and_negotiate_project", AsyncMock(return_value="taxbase")), \
         patch.object(db, "query_waitings", AsyncMock(return_value=[mock_w])):
        res = await waiting_query(project="taxbase")
        assert res.get("isError") is not True
        assert res["success"] is True
        assert res["total"] == 1
        assert res["items"][0]["id"] == "W-01"


@pytest.mark.asyncio
async def test_finding_query_scalar_and_clean_text():
    """F-API-08 / F-API-09: finding_query 支持标量 status='open' 及多状态列表，无 clean_text NameError。"""
    mock_f = Finding(
        id="F-01",
        source="audit",
        summary="发现项",
        severity=FindingSeverity.P2,
        status=FindingStatus.OPEN,
    )
    with patch("logbook.mcp_server.validate_and_negotiate_project", AsyncMock(return_value="taxbase")), \
         patch.object(db, "query_findings", AsyncMock(return_value=[mock_f])) as mock_q:
        # 1. 标量 status
        res1 = await finding_query(project="taxbase", status="open")
        assert res1.get("isError") is not True
        assert res1["success"] is True
        mock_q.assert_called_with("taxbase", status=[FindingStatus.OPEN], severity=None, limit=50)

        # 2. 列表 status
        res2 = await finding_query(project="taxbase", status=["open", "infix"])
        assert res2.get("isError") is not True
        assert res2["success"] is True


@pytest.mark.asyncio
async def test_task_query_scalar_and_review_status():
    """F-API-09: task_query 支持 review 状态，且支持标量与数组入参形态。"""
    mock_t = Task(
        id="T-01",
        title="测试任务",
        status=TaskStatus.REVIEW,
        priority=TaskPriority.P1,
    )
    with patch("logbook.mcp_server.validate_and_negotiate_project", AsyncMock(return_value="taxbase")), \
         patch.object(db, "query_tasks", AsyncMock(return_value=[mock_t])) as mock_q:
        # 标量 review
        res = await task_query(project="taxbase", status="review", priority="P1")
        assert res.get("isError") is not True
        assert res["success"] is True
        assert res["total"] == 1
        assert res["items"][0]["status"] == "review"
        mock_q.assert_called_with(
            "taxbase",
            status=[TaskStatus.REVIEW],
            priority=[TaskPriority.P1],
            batch_id=None,
            assignee=None,
            parent_id=None,
            limit=50,
        )


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
