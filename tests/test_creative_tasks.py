"""Unit tests for creative and narrative engineering task records (0.8.0 DataRecord)."""

import pytest
from logbook.models import DataRecord


def test_creative_task_record_payload():
    """测试叙事工程任务以 DataRecord 承载 (大纲、正文、润色、设定考据)。"""
    rec = DataRecord(
        project_id="novel",
        dataset_code="sys_task",
        doc_no="CH-001",
        entity_name="第一章：雪夜离宗",
        status="closed",
        payload={
            "task_type": "draft",
            "notes": "已完成 3200 字起草，场景冲突与章尾留钩自洽",
            "proof_link": "docs/chapter_01.md"
        }
    )
    assert rec.doc_no == "CH-001"
    assert rec.status == "closed"
    assert rec.payload["task_type"] == "draft"
    assert "3200 字" in rec.payload["notes"]
