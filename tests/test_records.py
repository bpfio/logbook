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
    assert normalize_dataset_category("unknown_domain") == "general"

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
