"""测试全实体 Agent IP 溯源审计 (0.8.0: Who + When + Where 审计三要素)。

验证对象:
1. 核心实体模型中 agent_ip / IP 字段缺省值、有效 IP 及超界校验
2. detect_caller_ip 多级自适应探测机制 (ContextVar 优先 -> SSH_CLIENT -> 本地套接字回退)
3. 核心写入工具 (message_send, lease_manage, record_upsert) IP 溯源审计
4. 时间戳与审计三要素完整性
"""

import os
import pytest
from pydantic import ValidationError

from logbook.models import (
    FileLeaseAcquire, FileLease,
    AgentMessageSend, AgentMessage,
    DataRecord, Project
)
from logbook.mcp_server import (
    detect_caller_ip,
    detect_node_ip,
    message_send,
    lease_manage,
    record_upsert,
)


def test_core_models_agent_ip_defaults_and_timestamps():
    """测试核心实体模型的 IP 字段与审计时间戳就绪。"""
    # 1. AgentMessageSend
    msg = AgentMessageSend(
        project_id="brix",
        from_agent="agy",
        to_agent="reviewer",
        subject="审计通知",
        content="正文"
    )
    assert msg.from_ip == "0.0.0.0"
    assert msg.to_ip == "0.0.0.0"

    # 2. FileLeaseAcquire
    lease = FileLeaseAcquire(
        project_id="brix",
        agent_name="agy",
        file_path="src/main.py"
    )
    assert lease.agent_ip == "0.0.0.0"

    # 3. DataRecord 时间戳
    rec = DataRecord(
        project_id="brix",
        dataset_code="sys_task",
        doc_no="T1",
        entity_name="任务1",
        payload={"agent_ip": "192.0.2.1"}
    )
    assert rec.created_at is not None
    assert rec.updated_at is not None
    assert rec.payload["agent_ip"] == "192.0.2.1"


def test_ip_field_validation_boundaries():
    """测试 IP 字段最大 45 字符边界 (支持最长 IPv6 地址)。"""
    oversized = "a" * 46
    with pytest.raises(ValidationError):
        FileLeaseAcquire(project_id="p", agent_name="a", file_path="f", agent_ip=oversized)

    with pytest.raises(ValidationError):
        AgentMessageSend(project_id="p", from_agent="a", to_agent="b", subject="s", content="c", from_ip=oversized)


def test_detect_caller_ip_priorities(monkeypatch):
    """测试 detect_caller_ip 的优先级判定。"""
    # 1. 显式入参最高优先
    assert detect_caller_ip("198.51.100.1") == "198.51.100.1"

    # 2. 环境变量 SSH_CLIENT
    monkeypatch.setenv("SSH_CLIENT", "203.0.113.19 54321 22")
    assert detect_caller_ip() == "203.0.113.19"
