"""Logbook 0.7.0 统一架构契约测试套件

测试范围:
1. 建议 1: MCP 工具集大归一 (默认 14 个核心工具，全部老工具在 tool_manager 中 100% 可调用)
2. 建议 2: 动态 JSON Schema 验证引擎 (必填结构校验与非标拦截)
3. 建议 3: 客户端 SDK Single-Flight 削峰与自适应指数退避重试
4. 建议 4: 5 大统一操作原语 (project_manage, group_manage, message_action, lease_manage, dataset_manage)
"""

import asyncio
import pytest
from logbook.mcp_server import mcp, CORE_TOOL_NAMES
from logbook.engine import fact_engine
from logbook.client import SingleFlight, retry_with_backoff


@pytest.mark.asyncio
async def test_mcp_tools_unification_14():
    """验证 0.7.0 MCP 工具归一化: 默认暴露 14 个核心工具。"""
    tools = await mcp.list_tools()
    tool_names = {t.name for t in tools}
    assert len(tools) == 14
    assert tool_names == CORE_TOOL_NAMES
    assert "record_upsert" in tool_names
    assert "record_search" in tool_names
    assert "dataset_manage" in tool_names
    assert "project_manage" in tool_names
    assert "group_manage" in tool_names
    assert "lease_manage" in tool_names
    assert "message_action" in tool_names


@pytest.mark.asyncio
async def test_legacy_tools_remain_callable():
    """验证历史 32 个老工具仍然注册在 tool_manager 中，保证向下兼容 100% 可调用。"""
    legacy_list = [
        "task_upsert",
        "tasks_bulk_upsert",
        "task_query",
        "finding_record",
        "finding_query",
        "waiting_record",
        "waiting_query",
        "batch_upsert",
        "batch_query",
        "devlog_record",
        "devlog_search",
        "rule_record",
        "rule_query",
        "research_record",
        "research_search",
        "research_query",
        "agent_message_send",
        "message_read",
        "message_archive",
        "lease_acquire",
        "lease_release",
        "lease_query",
        "group_record",
        "group_grant",
        "group_revoke",
        "group_member_add",
        "group_member_remove",
        "group_query",
        "principal_query",
        "project_init",
        "project_update",
        "dataset_upsert",
        "dataset_query",
    ]
    for name in legacy_list:
        assert mcp._tool_manager.get_tool(name) is not None, f"老工具 {name} 缺失！"


@pytest.mark.asyncio
async def test_json_schema_validation_engine():
    """验证动态 JSON Schema 校验引擎有效拦截非标 payload。"""
    # 注入假 schema 缓存验证机理
    fact_engine._schema_cache["sys_test_schema"] = {
        "type": "object",
        "required": ["req_str", "req_num"],
        "properties": {
            "req_str": {"type": "string", "minLength": 3},
            "req_num": {"type": "number"},
        },
    }

    # 1. 正常数据通过
    await fact_engine.validate_payload_schema(
        "sys_test_schema",
        {"req_str": "hello", "req_num": 123}
    )

    # 2. 缺失必填字段被拦截
    with pytest.raises(ValueError, match="必填字段 'req_num' 缺失或为空"):
        await fact_engine.validate_payload_schema(
            "sys_test_schema",
            {"req_str": "hello"}
        )

    # 3. 长度不达标被拦截
    with pytest.raises(ValueError, match="长度不能小于 3"):
        await fact_engine.validate_payload_schema(
            "sys_test_schema",
            {"req_str": "ab", "req_num": 1}
        )

    # 4. 类型错误被拦截
    with pytest.raises(ValueError, match="必须为数值类型"):
        await fact_engine.validate_payload_schema(
            "sys_test_schema",
            {"req_str": "abcd", "req_num": "not_a_number"}
        )

    # 5. OPT-02: 布尔类型与枚举值校验
    fact_engine._schema_cache["sys_test_bool_enum"] = {
        "type": "object",
        "required": ["is_active", "mode"],
        "properties": {
            "is_active": {"type": "boolean"},
            "mode": {"type": "string", "enum": ["auto", "manual"]},
        },
    }
    # 正常通过
    await fact_engine.validate_payload_schema(
        "sys_test_bool_enum",
        {"is_active": True, "mode": "auto"}
    )
    # 布尔类型错误拦截
    with pytest.raises(ValueError, match="必须为布尔类型"):
        await fact_engine.validate_payload_schema(
            "sys_test_bool_enum",
            {"is_active": "yes", "mode": "auto"}
        )
    # 枚举越界拦截
    with pytest.raises(ValueError, match="不在合法枚举范围"):
        await fact_engine.validate_payload_schema(
            "sys_test_bool_enum",
            {"is_active": False, "mode": "invalid_mode"}
        )

    # 6. OPT-02: 缓存失效机制
    assert "sys_test_bool_enum" in fact_engine._schema_cache
    fact_engine.invalidate_schema_cache("sys_test_bool_enum")
    assert "sys_test_bool_enum" not in fact_engine._schema_cache


@pytest.mark.asyncio
async def test_single_flight_merging():
    """验证 Single-Flight 并发请求合并: 10 个相同请求仅穿透执行 1 次。"""
    call_count = 0
    flight = SingleFlight()

    async def _heavy_work():
        nonlocal call_count
        call_count += 1
        await asyncio.sleep(0.05)
        return "result_ok"

    # 模拟 10 个并发协程同时拉取同一份数据
    tasks = [
        flight.do("brief:project_x", _heavy_work)
        for _ in range(10)
    ]
    results = await asyncio.gather(*tasks)

    assert len(results) == 10
    assert all(r == "result_ok" for r in results)
    assert call_count == 1, f"预期穿透执行 1 次，实际执行了 {call_count} 次！"


@pytest.mark.asyncio
async def test_retry_with_backoff():
    """验证指数退避重试器对网络偶发异常的自愈能力。"""
    attempts = 0

    async def _flakey_network():
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise ConnectionError("Mock network drop")
        return "recovered"

    result = await retry_with_backoff(_flakey_network, max_retries=3, base_delay=0.01)
    assert result == "recovered"
    assert attempts == 3


@pytest.mark.asyncio
async def test_bulk_upsert_schema_validation():
    """验证 records_bulk_upsert 批量导入严格执行 JSON Schema 强校验 (OPT-01)。"""
    from logbook.mcp_server import records_bulk_upsert
    from unittest.mock import patch

    # 预置 schema 缓存
    fact_engine._schema_cache["sys_strict_order"] = {
        "type": "object",
        "required": ["order_id", "buyer"],
        "properties": {
            "order_id": {"type": "string"},
            "buyer": {"type": "string", "minLength": 2},
        },
    }

    # 模拟项目协商通过
    with patch("logbook.mcp_server.validate_and_negotiate_project", return_value="test_proj"):
        # 1. 批量数据中若存在缺失 buyer 必填项，应原子拦截
        bad_records = [
            {"doc_no": "ORD-001", "dataset_code": "sys_strict_order", "payload": {"order_id": "O1", "buyer": "Alice"}},
            {"doc_no": "ORD-002", "dataset_code": "sys_strict_order", "payload": {"order_id": "O2"}},  # 缺少 buyer
        ]
        res = await records_bulk_upsert("test_proj", bad_records)
        assert res.get("isError") is True
        assert "第 2 条记录契约校验失败" in res.get("detail", "")
        assert "必填字段 'buyer' 缺失" in res.get("detail", "")
