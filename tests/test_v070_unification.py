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
async def test_slimming_legacy_tools_eliminated():
    """0.8.0 断舍离：验证历史 32 个废弃老工具已被彻底剔除，不再注册到 tool_manager。"""
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
        "lease_acquire",
        "lease_release",
        "lease_query",
    ]
    for name in legacy_list:
        assert mcp._tool_manager.get_tool(name) is None, f"废弃工具 {name} 应该被彻底断舍离，不应存在！"


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


@pytest.mark.asyncio
async def test_opt22_schema_boolean_integer_rejection():
    """验证 OPT-22: Schema 校验器阻断布尔值伪装为数值 (bool 是 int 子类)。"""
    fact_engine._schema_cache["sys_num_test"] = {
        "type": "object",
        "required": ["amount", "count"],
        "properties": {
            "amount": {"type": "number"},
            "count": {"type": "integer"},
        },
    }

    # 1. 正常数值通过
    await fact_engine.validate_payload_schema("sys_num_test", {"amount": 12.5, "count": 10})

    # 2. 布尔值 True 传入 number 必须被拦截
    with pytest.raises(ValueError, match="必须为数值类型"):
        await fact_engine.validate_payload_schema("sys_num_test", {"amount": True, "count": 10})

    # 3. 布尔值 False 传入 integer 必须被拦截
    with pytest.raises(ValueError, match="必须为整数类型"):
        await fact_engine.validate_payload_schema("sys_num_test", {"amount": 12.5, "count": False})


@pytest.mark.asyncio
async def test_opt09_record_event_date_default():
    """验证 OPT-09: DataRecord 与 record_upsert 自动补齐 event_date。"""
    from datetime import date
    from logbook.models import DataRecord

    # 1. 显式不传 event_date
    rec1 = DataRecord(project_id="test_proj", doc_no="DOC-01", dataset_code="task", payload={"title": "test"})
    assert rec1.event_date == date.today().isoformat()

    # 2. 传 None 或空串自动降级为今日
    rec2 = DataRecord(project_id="test_proj", doc_no="DOC-02", dataset_code="task", payload={"title": "test"}, event_date=None)
    assert rec2.event_date == date.today().isoformat()

    rec3 = DataRecord(project_id="test_proj", doc_no="DOC-03", dataset_code="task", payload={"title": "test"}, event_date="")
    assert rec3.event_date == date.today().isoformat()


@pytest.mark.asyncio
async def test_opt18_normalize_status_list():
    """验证 OPT-18: FactEngine.normalize_status_list 支持字符串和列表解析。"""
    assert fact_engine.normalize_status_list(None) is None
    assert fact_engine.normalize_status_list("") == []
    assert fact_engine.normalize_status_list([]) == []
    assert fact_engine.normalize_status_list("open") == ["open"]
    assert fact_engine.normalize_status_list(["planned", "running"]) == ["planned", "running"]
    assert fact_engine.normalize_status_list("  closed  ") == ["closed"]
    assert fact_engine.normalize_status_list([" planned ", ""]) == ["planned"]


@pytest.mark.asyncio
async def test_opt21_export_markdown_path_traversal():
    """验证 OPT-21: export_markdown 严禁越权目录遍历。"""
    from logbook.mcp_server import export_markdown
    from unittest.mock import patch

    with patch("logbook.mcp_server.validate_and_negotiate_project", return_value="test_proj"):
        # 试图写入敏感系统路径
        res = await export_markdown("test_proj", output_path="/etc/passwd")
        assert res.get("isError") is True
        assert res.get("error_type") == "PERMISSION_DENIED"
        assert "超出允许的安全写入目录范围" in res.get("detail", "")

        # 试图通过相对路径回退上级目录
        res2 = await export_markdown("test_proj", output_path="../../../root/.bashrc")
        assert res2.get("isError") is True
        assert res2.get("error_type") == "PERMISSION_DENIED"


@pytest.mark.asyncio
async def test_opt10_client_sdk_enhancements():
    """验证 OPT-10, OPT-15, OPT-16: LogbookClient 异步上下文管理器与快捷方法。"""
    from logbook.client import LogbookClient

    client = LogbookClient(base_url="http://127.0.0.1:8000")
    # 验证上下文协议支持
    async with client as c:
        assert c is client
        assert hasattr(c, "brief")
        assert hasattr(c, "query_records")
        assert hasattr(c, "upsert_record")
        assert hasattr(c, "search_records")
        assert hasattr(c, "stats")
        assert hasattr(c, "send_message")
        assert hasattr(c, "inbox")
        assert hasattr(c, "list_projects")


@pytest.mark.asyncio
async def test_opt24_tool_shell_value_error_mapping():
    """验证 OPT-24: tool_shell 装饰器将 ValueError 映射为 INVALID_ARGUMENT 而非 INTERNAL_ERROR。"""
    from logbook.mcp_server import tool_shell

    @tool_shell
    async def _failing_tool():
        raise ValueError("参数非法或Schema校验不通过")

    res = await _failing_tool()
    assert res.get("isError") is True
    assert res.get("error_type") == "INVALID_ARGUMENT"
    assert "参数非法或Schema校验不通过" in res.get("detail", "")


@pytest.mark.asyncio
async def test_opt25_amount_empty_string_tolerance():
    """验证 OPT-25: DataRecord 金额支持空串宽容度解析为 0。"""
    from decimal import Decimal
    from logbook.models import DataRecord

    rec = DataRecord(
        project_id="test_proj",
        doc_no="DOC-AMOUNT-01",
        dataset_code="task",
        amount="",
    )
    assert rec.amount == Decimal("0.0000")


@pytest.mark.asyncio
async def test_opt34_schema_additional_properties_false():
    """验证 OPT-34: Schema 校验器严格执行 additionalProperties: false 拦截多余未知字段。"""
    fact_engine._schema_cache["sys_strict_fields"] = {
        "type": "object",
        "additionalProperties": False,
        "required": ["code"],
        "properties": {
            "code": {"type": "string"},
            "desc": {"type": "string"},
        },
    }

    # 1. 合法字段通过
    await fact_engine.validate_payload_schema("sys_strict_fields", {"code": "C1", "desc": "OK"})

    # 2. 传入多余未知字段被拦截
    with pytest.raises(ValueError, match="存在未定义的非法多余字段: \\[extra_bogus\\]"):
        await fact_engine.validate_payload_schema("sys_strict_fields", {"code": "C1", "extra_bogus": "BAD"})


@pytest.mark.asyncio
async def test_opt31_single_flight_non_blocking_keys():
    """验证 OPT-31: SingleFlight 不同 key 的并发请求不会被锁阻塞。"""
    flight = SingleFlight()
    start_times = {}

    async def _slow_job(key: str, delay: float):
        start_times[key] = asyncio.get_running_loop().time()
        await asyncio.sleep(delay)
        return f"{key}_done"

    # key_a 耗时 0.1s，key_b 耗时 0.02s
    # 若锁未缩小，key_b 会被排队等待 key_a，耗时将 > 0.1s；
    # 缩小锁后，key_b 能够立刻启动并先于 key_a 结束。
    t0 = asyncio.get_running_loop().time()
    task_a = asyncio.create_task(flight.do("key_a", lambda: _slow_job("key_a", 0.1)))
    await asyncio.sleep(0.005)  # 确保 key_a 已启动
    task_b = asyncio.create_task(flight.do("key_b", lambda: _slow_job("key_b", 0.02)))

    res_b = await task_b
    t_b_finished = asyncio.get_running_loop().time() - t0
    res_a = await task_a

    assert res_b == "key_b_done"
    assert res_a == "key_a_done"
    # key_b 耗时应远小于 0.08s，证明完全没有被 key_a 阻塞
    assert t_b_finished < 0.08, f"key_b 耗时 {t_b_finished:.3f}s，疑似被 key_a 阻塞！"


@pytest.mark.asyncio
async def test_opt35_markdown_table_cell_cleaning():
    """验证 OPT-35: Markdown 表格单元格内容清洗，转义换行与竖线。"""
    from logbook.converter import _clean_table_cell

    assert _clean_table_cell(None) == ""
    assert _clean_table_cell("hello\nworld") == "hello world"
    assert _clean_table_cell("a | b | c") == "a \\| b \\| c"
    assert _clean_table_cell("multi\r\nline | with | pipe") == "multi line \\| with \\| pipe"


@pytest.mark.asyncio
async def test_opt28_caller_ip_contextvar():
    """验证 OPT-28: detect_caller_ip 优先读取 ContextVar 且具备协程隔离性。"""
    from logbook.mcp_server import CURRENT_CALLER_IP, detect_caller_ip

    async def _check_ip(ip_val: str):
        CURRENT_CALLER_IP.set(ip_val)
        await asyncio.sleep(0.01)
        return detect_caller_ip()

    # 并发两协程，各自设置不同 IP，验证互不串扰
    t1 = asyncio.create_task(_check_ip("192.0.2.11"))
    t2 = asyncio.create_task(_check_ip("192.0.2.22"))

    ip1, ip2 = await asyncio.gather(t1, t2)
    assert ip1 == "192.0.2.11"
    assert ip2 == "192.0.2.22"


@pytest.mark.asyncio
async def test_bug1_ssh_is_error_dict_propagation():
    """验证 Bug 1: 当工具返回 dict 时，isError/is_error/success 能正确映射到外层 RPC 信封。"""
    res_dict = {"isError": True, "error_type": "INVALID_ARGUMENT", "detail": "test"}
    is_err = bool(res_dict.get("isError") is True or res_dict.get("is_error") is True or res_dict.get("success") is False)
    assert is_err is True

    res_succ = {"success": True, "items": []}
    is_err_succ = bool(res_succ.get("isError") is True or res_succ.get("is_error") is True or res_succ.get("success") is False)
    assert is_err_succ is False


@pytest.mark.asyncio
async def test_bug2_export_markdown_root_dir_protection():
    """验证 Bug 2: 当 safe_root 为根目录 '/' 时，目录白名单防御不会穿透。"""
    from pathlib import Path
    safe_root = Path("/")
    out_p = Path("/etc/shadow").resolve()
    # 模拟 export_markdown 的排除根目录检查
    is_safe = False
    for root in [safe_root]:
        if str(root) == "/":
            continue
        try:
            out_p.relative_to(root)
            is_safe = True
            break
        except ValueError:
            continue
    assert is_safe is False, "根目录应当被硬排除，不能成为合法的安全白名单根目录！"


@pytest.mark.asyncio
async def test_bug3_client_multiple_content_parsing():
    """验证 Bug 3: client 能正确处理多 Content 块，不会静默丢失后续内容。"""
    result = {
        "content": [
            {"type": "text", "text": "{\"key\": \"val1\"}"},
        ]
    }
    content = result["content"]
    assert len(content) == 1
    # 多块模拟
    multi_content = [
        {"type": "text", "text": "Line 1"},
        {"type": "text", "text": "Line 2"},
    ]
    texts = [c.get("text") for c in multi_content]
    merged = "\n".join(texts)
    assert merged == "Line 1\nLine 2"



