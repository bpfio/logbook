"""Logbook 通用事实引擎与数据集 (Datasets & Records) 测试套件

覆盖:
1. DatasetCategory & RecordStatus 枚举与鲁棒归一化 (Normalizer)
2. Dataset 元数据契约与 code 正则校验 (2~64 位字符)
3. DataRecord 强脊梁与软肋骨契约 (高精度 Decimal 金额无浮点误差、日期转换)
4. Database 层数据表自动就绪 (ensure_datasets_and_records_schema) 与 CRUD
5. Master-Detail 主子表分录 (parent_id) 级联与关联
6. 动态属性 JSONB 检索 (payload @>) 与全文检索 (tsv_content)
7. 聚合计算 (aggregate_record_stats: count, sum, min, max, avg, group_by)
8. MCP 工具层调用 (dataset_upsert, dataset_query, record_upsert, record_query, record_stats)
"""

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from pydantic import ValidationError

from logbook.models import (
    Dataset,
    DatasetCategory,
    DataRecord,
    RecordStatus,
)
from logbook.normalizer import (
    normalize_dataset_category,
    normalize_record_status,
)
from logbook.db import db
from logbook.mcp_server import (
    dataset_upsert,
    dataset_query,
    record_upsert,
    record_query,
    record_stats,
    record_search,
    records_bulk_upsert,
    tasks_bulk_upsert,
    mcp,
)


# =============================================================================
# 一、 契约与归一化单元测试 (Pure Unit Tests)
# =============================================================================

def test_dataset_category_and_record_status_enums():
    """测试数据集与单据状态枚举及归一化。"""
    assert DatasetCategory.FINANCE == "finance"
    assert DatasetCategory.OSS_METRICS == "oss_metrics"
    assert DatasetCategory.TRADE == "trade"
    assert DatasetCategory.TAX == "tax"
    assert DatasetCategory.RESEARCH == "research"
    assert DatasetCategory.GENERAL == "general"

    assert normalize_dataset_category("financial") == "finance"
    assert normalize_dataset_category("oss") == "oss_metrics"
    assert normalize_dataset_category("benchmark") == "oss_metrics"
    assert normalize_dataset_category("customs") == "trade"
    with pytest.raises(ValueError, match="无效的 dataset category"):
        normalize_dataset_category("unknown_domain")
    assert normalize_dataset_category("unknown_domain", default="general") == "general"

    assert RecordStatus.RECORDED == "recorded"
    assert RecordStatus.RECONCILED == "reconciled"
    assert RecordStatus.VERIFIED == "verified"
    assert RecordStatus.CANCELLED == "cancelled"

    assert normalize_record_status("record") == "recorded"
    assert normalize_record_status("audited") == "reconciled"
    assert normalize_record_status("valid") == "verified"
    assert normalize_record_status("void") == "cancelled"
    assert normalize_record_status("canceled") == "cancelled"


def test_dataset_model_validation():
    """测试 Dataset 模型编码规范。"""
    # 合法 code
    ds = Dataset(
        code="safe_forex.v1",
        name="涉外收汇申报单据",
        category="finance",
        field_schema=[{"key": "exchange_rate", "label": "汇率", "type": "number"}],
        alias_map={"结汇金额": "amount", "申报号": "doc_no"},
        description="用于人行跨境涉外资金收汇登记核销",
    )
    assert ds.code == "safe_forex.v1"
    assert ds.alias_map["结汇金额"] == "amount"

    # 非法 code (含空格或特殊符号)
    with pytest.raises(ValidationError):
        Dataset(code="Invalid Code!", name="测试")

    with pytest.raises(ValidationError):
        Dataset(code="a", name="测试")  # 长度小于 2


def test_data_record_model_amount_precision():
    """测试 DataRecord 核心金额 NUMERIC 高精度无浮点误差。"""
    # 字符串输入转 Decimal
    rec = DataRecord(
        project_id="brix",
        dataset_code="safe_forex",
        doc_no="FX-202610-001",
        amount="123456789.9876",
        currency="USD",
        event_date="2026-10-19",
        payload={"bank": "BOC", "contract_no": "CT-8899"},
    )
    assert isinstance(rec.amount, Decimal)
    assert rec.amount == Decimal("123456789.9876")
    assert rec.currency == "USD"
    assert rec.payload["bank"] == "BOC"

    # 浮点输入安全解析
    rec2 = DataRecord(
        project_id="brix",
        dataset_code="oss_benchmark",
        doc_no="BENCH-QPS-001",
        amount=9988.5,
        currency="QPS",
        event_date=date(2026, 10, 19),
    )
    assert isinstance(rec2.amount, Decimal)
    assert rec2.amount == Decimal("9988.5")


@pytest.mark.asyncio
async def test_mcp_record_tools_negotiation_unit():
    """测试 MCP 工具显式传参校验 (无 DB 依赖)。"""
    # 缺少 project 触发 ProjectNegotiationError
    res1 = await record_upsert(
        project="",
        dataset_code="test",
        doc_no="DOC-001",
        event_date="2026-10-19",
    )
    assert res1.get("isError") is True

    res2 = await record_query(project="")
    assert res2.get("isError") is True

    res3 = await record_stats(project="")
    assert res3.get("isError") is True

    res4 = await record_search(project="", query="test")
    assert res4.get("isError") is True


@pytest.mark.asyncio
async def test_search_records_sql_params_unit():
    """单元测试: 验证 search_records 各分支参数绑定严格对齐，零未引用占位符，且 0 命中不穿透。"""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []

    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__.return_value = mock_conn

    with patch.object(db, "_pool", mock_pool), \
         patch.object(db, "ensure_datasets_and_records_schema", AsyncMock()):

        # 1. 向量分支 (无 dataset_code): 必须有且仅有 $1 (project_id), $2 (vec), $3 (limit), $4 (offset)
        res = await db.search_records(project_id="brix", query_vector=[0.1] * 768)
        assert res == []
        assert mock_conn.fetch.call_count == 1
        sql, *params = mock_conn.fetch.call_args[0]
        assert len(params) == 4
        assert params[0] == "brix"
        assert params[1].startswith("[0.1,")
        assert params[2] == 10
        assert params[3] == 0
        assert "$1" in sql and "$2::vector" in sql and "LIMIT $3 OFFSET $4" in sql

        # 2. 向量分支 (带 dataset_code): 必须有且仅有 $1 (project_id), $2 (dataset_code), $3 (vec), $4 (limit), $5 (offset)
        mock_conn.fetch.reset_mock()
        res = await db.search_records(project_id="taxbase", dataset_code="tax_reg", query_vector=[0.1] * 768)
        assert res == []
        assert mock_conn.fetch.call_count == 1
        sql, *params = mock_conn.fetch.call_args[0]
        assert len(params) == 5
        assert params[0] == "taxbase"
        assert params[1] == "tax_reg"
        assert params[2].startswith("[0.1,")
        assert params[3] == 10
        assert params[4] == 0
        assert "$1" in sql and "dataset_code = $2" in sql and "$3::vector" in sql and "LIMIT $4 OFFSET $5" in sql

        # 3. 降级全文检索 (带 query_text): 必须有且仅有 $1 (project_id), $2 (query_text), $3 (limit), $4 (offset)
        mock_conn.fetch.reset_mock()
        res = await db.search_records(project_id="taxbase", query_text="增值税")
        assert res == []
        assert mock_conn.fetch.call_count == 1
        sql, *params = mock_conn.fetch.call_args[0]
        assert len(params) == 4
        assert params[0] == "taxbase"
        assert params[1] == "增值税"
        assert "$1" in sql and "plainto_tsquery('simple', $2)" in sql and "LIMIT $3 OFFSET $4" in sql

        # 4. 常规浏览 (无 vector、无 query_text): 必须有且仅有 $1 (project_id), $2 (limit), $3 (offset)
        mock_conn.fetch.reset_mock()
        res = await db.search_records(project_id="taxbase")
        assert res == []
        assert mock_conn.fetch.call_count == 1
        sql, *params = mock_conn.fetch.call_args[0]
        assert len(params) == 3
        assert params[0] == "taxbase"
        assert "$1" in sql and "LIMIT $2 OFFSET $3" in sql

        # 5. 向量检索 + min_score 截断与 ef_search 放大 (F-SEARCH-02 / F-SEARCH-03)
        mock_conn.fetch.reset_mock()
        mock_conn.execute.reset_mock()
        mock_conn.fetch.return_value = [
            {"id": "rec_1", "doc_no": "D1", "score": 0.58, "payload": "{}"},
            {"id": "rec_2", "doc_no": "D2", "score": 0.35, "payload": "{}"},
        ]
        res = await db.search_records(
            project_id="taxbase",
            query_vector=[0.1] * 768,
            min_score=0.45,
            limit=50,
        )
        # 必须过滤掉 score 0.35 的低相关记录，仅保留 0.58
        assert len(res) == 1
        assert res[0]["doc_no"] == "D1"
        assert res[0]["score"] == 0.58

        # 验证 ef_search 设置: limit=50 -> ef_search = max(40, min(400, 50*2)) = 100
        mock_conn.execute.assert_called_with("SET hnsw.ef_search = 100;")
        sql, *params = mock_conn.fetch.call_args[0]
        # 参数结构: $1 (project_id), $2 (vec), $3 (min_score), $4 (limit), $5 (offset)
        assert len(params) == 5
        assert params[2] == 0.45
        assert "(1 - (embedding <=> $2::vector)) >= $3" in sql
        assert "LIMIT $4 OFFSET $5" in sql

        # 6. 全文检索 + min_score 截断
        mock_conn.fetch.reset_mock()
        mock_conn.fetch.return_value = [
            {"id": "rec_1", "doc_no": "D1", "score": 0.52, "payload": "{}"},
            {"id": "rec_2", "doc_no": "D2", "score": 0.20, "payload": "{}"},
        ]
        res = await db.search_records(
            project_id="taxbase",
            query_text="增值税",
            min_score=0.40,
        )
        assert len(res) == 1
        assert res[0]["doc_no"] == "D1"
        sql, *params = mock_conn.fetch.call_args[0]
        # 参数结构: $1 (project_id), $2 (query_text), $3 (min_score), $4 (limit), $5 (offset)
        assert len(params) == 5
        assert params[1] == "增值税"
        assert params[2] == 0.40
        assert "ts_rank_cd(tsv_content, plainto_tsquery('simple', $2)) >= $3" in sql


@pytest.mark.asyncio
async def test_validation_layer_rejections_and_envelope_unit():
    """单元测试: 验证 F-API-01~06 统一结构化错误信封与参数强校验拦截。"""
    with patch("logbook.mcp_server.validate_and_negotiate_project", AsyncMock(return_value="test_proj")), \
         patch.object(db, "ensure_project", AsyncMock()), \
         patch.object(db, "ensure_datasets_and_records_schema", AsyncMock()), \
         patch("logbook.mcp_server.get_embedding", AsyncMock(return_value=MagicMock(embedding=None))):

        # 1. F-SEARCH-04: min_score 非法值拦截 (1.5 / -0.1 / True)
        res1 = await record_search(project="test_proj", query="test", min_score=1.5)
        assert res1.get("isError") is True
        assert res1.get("error_type") == "INVALID_ARGUMENT"
        assert "0.0 至 1.0" in res1.get("detail", "")

        res_bool = await record_search(project="test_proj", query="test", min_score=True)
        assert res_bool.get("isError") is True
        assert res_bool.get("error_type") == "INVALID_ARGUMENT"

        # 2. F-API-03: limit <= 0 拦截
        res_lim = await record_search(project="test_proj", query="test", limit=-1)
        assert res_lim.get("isError") is True
        assert res_lim.get("error_type") == "INVALID_ARGUMENT"
        assert "limit" in res_lim.get("detail", "")

        # 3. F-API-01: record_query 非法状态拦截
        res_rq = await record_query(project="test_proj", status="typo_status")
        assert res_rq.get("isError") is True
        assert res_rq.get("error_type") == "INVALID_ARGUMENT"
        assert "record status" in res_rq.get("detail", "")

        # 4. F-API-01: record_stats 非法 group_by 拦截
        res_rs = await record_stats(project="test_proj", group_by="invalid_dim")
        assert res_rs.get("isError") is True
        assert res_rs.get("error_type") == "INVALID_ARGUMENT"
        assert "group_by" in res_rs.get("detail", "")

        # 5. F-API-05: records_bulk_upsert 缺失 doc_no 拒绝伪造单号
        res_bulk_nodoc = await records_bulk_upsert(
            project="test_proj",
            dataset_code="test_ds",
            records=[{"event_date": "2026-11-06"}],  # 缺失 doc_no
        )
        assert res_bulk_nodoc.get("isError") is True
        assert res_bulk_nodoc.get("error_type") == "INVALID_ARGUMENT"
        assert "doc_no" in res_bulk_nodoc.get("detail", "")

        # 6. F-API-06: tasks_bulk_upsert 校验枚举合法性
        res_task_bad = await tasks_bulk_upsert(
            project="test_proj",
            tasks=[{"id": "t1", "title": "test", "status": "wrong_status"}],
        )
        assert res_task_bad.get("isError") is True
        assert res_task_bad.get("error_type") == "INVALID_ARGUMENT"
        assert "wrong_status" in res_task_bad.get("detail", "")

        # 7. F-API-02: FastMCP 顶层包装器全局捕获并输出结构化 JSON 信封
        class FakeParams:
            name = "record_search"
            arguments = {"project": 123}  # 非法参数类型触发 Pydantic ValidationError

        call_res = await mcp._handle_call_tool(None, FakeParams())
        import json
        assert call_res.is_error is True
        parsed = json.loads(call_res.content[0].text)
        assert parsed.get("isError") is True
        assert parsed.get("error_type") == "INVALID_ARGUMENT"
        assert parsed.get("tool") == "record_search"


# =============================================================================
# 二、 数据库与 MCP 交互集成测试 (Integration Tests)
# =============================================================================

@pytest.mark.integration
@pytest.mark.asyncio
async def test_dataset_and_record_lifecycle_integration():
    """测试数据集与事实记录端到端全生命周期。"""
    # 1. 注册数据集
    ds_code = "unit_safe_forex"
    ds = Dataset(
        code=ds_code,
        name="人行涉外收汇申报",
        category="finance",
        field_schema=[
            {"key": "bank", "label": "经办银行", "type": "string"},
            {"key": "rate", "label": "核销汇率", "type": "number"},
        ],
        alias_map={"申报号": "doc_no", "结汇金额": "amount"},
        description="单元测试外汇收支",
    )
    created_ds = await db.upsert_dataset(ds)
    assert created_ds["code"] == ds_code
    assert created_ds["name"] == "人行涉外收汇申报"

    # 查询数据集
    query_ds = await db.get_dataset(ds_code)
    assert query_ds is not None
    assert query_ds["alias_map"]["申报号"] == "doc_no"

    # 2. 插入主单据 (Master)
    master_rec = DataRecord(
        project_id="test_proj",
        dataset_code=ds_code,
        doc_no="INV-202610-001",
        entity_name="Acme Corp Global",
        amount=Decimal("50000.0000"),
        currency="USD",
        event_date="2026-10-15",
        status="recorded",
        payload={"bank": "Bank of China", "customs_code": "0101", "port": "Shanghai"},
    )
    m_res = await db.upsert_record(master_rec)
    assert m_res["id"] is not None
    assert m_res["doc_no"] == "INV-202610-001"
    master_id = m_res["id"]

    # 3. 插入子明细单据 (Detail: parent_id)
    child1 = DataRecord(
        project_id="test_proj",
        dataset_code=ds_code,
        parent_id=master_id,
        doc_no="INV-202610-001#1",
        entity_name="Acme Corp Global",
        amount=Decimal("30000.0000"),
        currency="USD",
        event_date="2026-10-15",
        payload={"item": "Industrial Controller A", "qty": 10},
    )
    child2 = DataRecord(
        project_id="test_proj",
        dataset_code=ds_code,
        parent_id=master_id,
        doc_no="INV-202610-001#2",
        entity_name="Acme Corp Global",
        amount=Decimal("20000.0000"),
        currency="USD",
        event_date="2026-10-15",
        payload={"item": "Sensor Module B", "qty": 50},
    )
    cnt = await db.bulk_import_records([child1, child2])
    assert cnt == 2

    # 4. 查询子单据
    children = await db.query_records(
        project_id="test_proj",
        dataset_code=ds_code,
        parent_id=master_id,
    )
    assert len(children) == 2

    # 5. JSONB @> 属性过滤
    port_records = await db.query_records(
        project_id="test_proj",
        dataset_code=ds_code,
        payload_filter={"port": "Shanghai"},
    )
    assert len(port_records) >= 1
    assert port_records[0]["doc_no"] == "INV-202610-001"

    # 6. 全文检索 (tsv_content @@ query_text)
    kw_records = await db.query_records(
        project_id="test_proj",
        query_text="Acme Corp",
    )
    assert len(kw_records) >= 1

    # 7. 统计汇总 (aggregate_record_stats)
    stats = await db.aggregate_record_stats(
        project_id="test_proj",
        dataset_code=ds_code,
        group_by="currency",
    )
    assert stats["summary"]["total_count"] >= 3
    assert stats["summary"]["total_amount"] >= 100000.0


@pytest.mark.integration
@pytest.mark.asyncio
async def test_mcp_tools_record_and_dataset():
    """测试通过 FastMCP 工具层调用 dataset_* 与 record_* 工具。"""
    # 1. 注册数据集 MCP 工具
    ds_res = await dataset_upsert(
        code="mcp_oss_benchmark",
        name="开源项目基准测试",
        category="oss_metrics",
        field_schema=[{"key": "latency_p99", "label": "P99延迟", "type": "number"}],
        alias_map={"分位耗时": "latency_p99"},
        description="系统与中间件性能评测数据集",
    )
    assert ds_res["success"] is True
    assert ds_res["dataset"]["code"] == "mcp_oss_benchmark"

    # 2. 查询数据集 MCP 工具
    query_res = await dataset_query(code="mcp_oss_benchmark")
    assert query_res["success"] is True
    assert len(query_res["items"]) == 1

    # 3. 写入单据 MCP 工具 (需要合法 project 代号，使用内置 'logbook')
    rec_res = await record_upsert(
        project="logbook",
        dataset_code="mcp_oss_benchmark",
        doc_no="BENCH-POSTGRES-18-01",
        event_date="2026-10-19",
        amount=145000.0,
        currency="QPS",
        entity_name="postgresql-18-rc1",
        payload={"commit": "abc1234", "workers": 16, "latency_p99": 0.85},
    )
    assert rec_res["success"] is True
    assert rec_res["record"]["doc_no"] == "BENCH-POSTGRES-18-01"
    assert rec_res["record"]["amount"] == 145000.0

    # 4. 检索单据 MCP 工具
    q_rec = await record_query(
        project="logbook",
        dataset_code="mcp_oss_benchmark",
        doc_no="BENCH-POSTGRES-18-01",
    )
    assert q_rec["success"] is True
    assert len(q_rec["items"]) == 1
    assert q_rec["items"][0]["payload"]["latency_p99"] == 0.85

    # 5. 统计指标 MCP 工具
    st_res = await record_stats(
        project="logbook",
        dataset_code="mcp_oss_benchmark",
        group_by="currency",
    )
    assert st_res["success"] is True
    assert st_res["stats"]["summary"]["total_count"] >= 1
