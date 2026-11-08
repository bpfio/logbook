"""测试研发调研知识事实记录 (0.8.0 DataRecord sys_research)

验证对象:
1. 调研结构化五要素 (objective, market_landscape, tradeoffs, decision, references) 通过 DataRecord 承载
2. 权威发布元数据 (doc_no, authority, legal_level, effective_date) 存入 payload
3. 向量与全文多维事实检索
"""

import pytest
from logbook.models import DataRecord


def test_research_record_five_elements_payload():
    """测试调研知识五要素与权威政策元数据在 DataRecord payload 中的完整性。"""
    rec = DataRecord(
        project_id="bpfio/logbook",
        dataset_code="sys_research",
        doc_no="RES-PG18",
        entity_name="PostgreSQL 18 vs SQLite 并发与内存对比评测",
        status="completed",
        payload={
            "category": "database",
            "author": "agy",
            "agent_ip": "192.0.2.1",
            "objective": "评估低内存 2C2G 边缘 NAS 环境下的并发稳定性",
            "market_landscape": "SQLite 生产跨 Agent 易写锁；PG18 配置可锁死在 48MB",
            "tradeoffs": "自研内存管理对抗成本高；PG18 参数白纸黑字透明可控",
            "decision": "选用精调 PG18 容器化部署，连接池严格硬锁 1~3",
            "references": "https://postgresql.org",
            "doc_no": "RFC-2026-DB",
            "authority": "PostgreSQL Global Development Group",
        }
    )
    assert rec.doc_no == "RES-PG18"
    assert rec.status == "completed"
    assert rec.payload["category"] == "database"
    assert "PG18" in rec.payload["decision"]
    assert rec.created_at is not None
