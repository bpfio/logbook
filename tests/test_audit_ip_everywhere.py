"""测试全实体 Agent IP 溯源审计 (06_migration / audit triad: Who + When + Where)。

验证对象:
1. 7 大业务与系统实体模型中 agent_ip 缺省值、有效 IP 及超界校验
2. detect_caller_ip 三级自适应探测机制 (显式传参 -> SSH_CLIENT/SSH_CONNECTION 环境变量 -> 本地套接字回退)
3. 8 个核心 MCP 写入工具 agent_ip 参数契约与向后兼容性
4. 时间戳 (created_at / updated_at / discovered_at 等) 与 agent_ip 审计三要素完整性
"""

import os
import pytest
from pydantic import ValidationError

from logbook.models import (
    Task, TaskStatus, TaskPriority, TaskType,
    Finding, FindingSeverity, FindingStatus,
    Waiting, WaitingCategory, WaitingStatus,
    Batch, BatchStatus,
    DevLog, DevLogVisibility,
    FileLeaseAcquire, FileLease,
    AgentMessageSend, AgentMessage,
)
from logbook.mcp_server import (
    detect_caller_ip,
    detect_node_ip,
    task_upsert,
    tasks_bulk_upsert,
    finding_record,
    waiting_record,
    batch_upsert,
    devlog_record,
    lease_acquire,
    message_send,
)


def test_all_models_agent_ip_defaults_and_timestamps():
    """测试所有 7+ 个核心实体模型的 agent_ip 缺省值 ('0.0.0.0') 与审计时间戳就绪。"""
    # 1. Task
    t = Task(id="T-AUDIT-1", title="审计任务", status=TaskStatus.PLANNED)
    assert t.agent_ip == "0.0.0.0"
    assert t.created_at is not None
    assert t.updated_at is not None

    # 2. Finding
    f = Finding(id="F-AUDIT-1", source="audit", summary="测试缺陷")
    assert f.agent_ip == "0.0.0.0"
    assert f.discovered_at is not None

    # 3. Waiting
    w = Waiting(id="W-AUDIT-1", description="测试阻塞项")
    assert w.agent_ip == "0.0.0.0"
    assert w.blocked_at is not None

    # 4. Batch
    b = Batch(id="B-AUDIT-1", title="测试批次")
    assert b.agent_ip == "0.0.0.0"
    assert b.created_at is not None

    # 5. DevLog
    d = DevLog(
        project_id="logbook",
        title="测试手记",
        problem="现象",
        root_cause="根因",
        solution="方案",
        evidence="证据",
    )
    assert d.agent_ip == "0.0.0.0"
    assert d.occurred_at is not None
    assert d.created_at is not None
    assert d.updated_at is not None

    # 6. FileLeaseAcquire & FileLease
    req = FileLeaseAcquire(project_id="logbook", agent_name="agy", file_path="src/db.py")
    assert req.agent_ip == "0.0.0.0"

    lease = FileLease(
        id=1,
        project_id="logbook",
        agent_name="agy",
        file_path="src/db.py",
        lease_expires_at=t.created_at,
    )
    assert lease.agent_ip == "0.0.0.0"
    assert lease.created_at is not None

    # 7. AgentMessageSend
    msg = AgentMessageSend(
        project_id="logbook",
        from_agent="agy",
        to_agent="zcode",
        subject="测试",
        content="内容",
    )
    assert msg.from_ip == "0.0.0.0"
    assert msg.to_ip == "0.0.0.0"


def test_agent_ip_validation_and_bounds():
    """测试 IPv4, IPv6 正常赋值及超过 45 字符抛出 ValidationError。"""
    # 正常 IPv4
    t_v4 = Task(id="T-V4", title="IPv4任务", agent_ip="192.168.1.100")
    assert t_v4.agent_ip == "192.168.1.100"

    # 正常 IPv6
    t_v6 = Task(id="T-V6", title="IPv6任务", agent_ip="fe80::1ff:fe23:4567:890a")
    assert t_v6.agent_ip == "fe80::1ff:fe23:4567:890a"

    # 超界 (>45 字符) 校验阻断
    oversized = "1" * 46
    with pytest.raises(ValidationError):
        Task(id="T-ERR", title="超界任务", agent_ip=oversized)
    with pytest.raises(ValidationError):
        Finding(id="F-ERR", source="audit", summary="超界", agent_ip=oversized)
    with pytest.raises(ValidationError):
        Waiting(id="W-ERR", description="超界", agent_ip=oversized)
    with pytest.raises(ValidationError):
        Batch(id="B-ERR", title="超界", agent_ip=oversized)
    with pytest.raises(ValidationError):
        DevLog(
            project_id="logbook",
            title="超界",
            problem="P",
            root_cause="R",
            solution="S",
            evidence="E",
            agent_ip=oversized,
        )


def test_detect_caller_ip_mechanisms(monkeypatch):
    """测试 detect_caller_ip 的三级探测优先级。"""
    # 1. 显式提供 IP，直接优先返回且做 strip
    assert detect_caller_ip(" 192.168.1.50 ") == "192.168.1.50"
    assert detect_caller_ip("2001:db8::1") == "2001:db8::1"

    # 2. 未显式传参时，检测 SSH_CLIENT 环境变量
    monkeypatch.setenv("SSH_CLIENT", "10.0.0.25 43210 22")
    assert detect_caller_ip() == "10.0.0.25"
    assert detect_caller_ip("") == "10.0.0.25"

    # 3. SSH_CLIENT 不在，检测 SSH_CONNECTION 环境变量
    monkeypatch.delenv("SSH_CLIENT", raising=False)
    monkeypatch.setenv("SSH_CONNECTION", "10.0.0.30 54321 10.0.0.1 22")
    assert detect_caller_ip() == "10.0.0.30"

    # 4. 无 SSH 环境变量，回退至本地套接字探测结果
    monkeypatch.delenv("SSH_CONNECTION", raising=False)
    fallback_ip = detect_caller_ip()
    assert isinstance(fallback_ip, str)
    assert len(fallback_ip) > 0
    assert fallback_ip != "0.0.0.0"


@pytest.mark.asyncio
async def test_mcp_write_tools_agent_ip_support():
    """测试所有 8 个核心 MCP 写入工具均支持 agent_ip 可选入参且不报错。"""
    # 空 project 调用均会走 negotiate / 校验并返回结构化错误，验证入参已被正确绑定且未发生 TypeError

    # 1. task_upsert
    res1 = await task_upsert(project="", id="T1", title="T", status="planned", agent_ip="192.168.1.88")
    assert res1.get("isError") is True

    # 2. tasks_bulk_upsert
    res2 = await tasks_bulk_upsert(project="", tasks=[{"id": "T2", "title": "T2"}], agent_ip="192.168.1.88")
    assert res2.get("isError") is True

    # 3. finding_record
    res3 = await finding_record(project="", id="F1", summary="F", agent_ip="192.168.1.88")
    assert res3.get("isError") is True

    # 4. waiting_record
    res4 = await waiting_record(project="", id="W1", description="W", agent_ip="192.168.1.88")
    assert res4.get("isError") is True

    # 5. batch_upsert
    res5 = await batch_upsert(project="", id="B1", title="B", agent_ip="192.168.1.88")
    assert res5.get("isError") is True

    # 6. devlog_record
    res6 = await devlog_record(
        project="", title="D", problem="P", root_cause="R", solution="S", evidence="E", agent_ip="192.168.1.88"
    )
    assert res6.get("isError") is True

    # 7. lease_acquire
    res7 = await lease_acquire(project="", agent_name="agy", file_path="src/db.py", agent_ip="192.168.1.88")
    assert res7.get("isError") is True

    # 8. message_send
    res8 = await message_send(project="", from_agent="agy", to_agent="zcode", subject="S", content="C", from_ip="192.168.1.88")
    assert res8.get("isError") is True
