"""测试 Logbook 0.3.0 多 Agent 对讲信箱与代码文件租约软锁功能。"""

import pytest
from logbook.models import (
    Task, TaskStatus, TaskPriority, TaskType,
    AgentMessageSend, AgentMessage,
    FileLeaseAcquire, FileLease
)
from logbook.normalizer import normalize_status
from logbook.mcp_server import (
    message_send,
    message_inbox,
    message_read,
    lease_acquire,
    lease_release,
    lease_query,
    task_upsert,
)


def test_models_and_normalizer_review_status():
    """测试 TaskStatus.REVIEW 枚举与 Normalizer 状态归一化支持。"""
    assert TaskStatus.REVIEW == "review"
    assert normalize_status("review") == "review"
    assert normalize_status("in_review") == "review"
    assert normalize_status("reviewing") == "review"

    t = Task(
        id="T-TEST-01",
        title="测试待验收任务",
        status=TaskStatus.REVIEW,
        assignee="codebuddy",
        reviewer="zcode",
    )
    assert t.status == TaskStatus.REVIEW
    assert t.assignee == "codebuddy"
    assert t.reviewer == "zcode"


def test_message_and_lease_models():
    """测试 AgentMessage 与 FileLease 模型实例化与序列化。"""
    msg_send = AgentMessageSend(
        project_id="logbook",
        from_agent="codebuddy",
        to_agent="zcode",
        subject="测试信件",
        content="请验收代码",
        task_id="L13.1",
        thread_id="th-01",
    )
    assert msg_send.from_agent == "codebuddy"
    assert msg_send.to_agent == "zcode"
    assert msg_send.task_id == "L13.1"

    lease_req = FileLeaseAcquire(
        project_id="logbook",
        agent_name="codebuddy",
        file_path="src/logbook/db.py",
        duration_seconds=600,
    )
    assert lease_req.duration_seconds == 600
    assert lease_req.file_path == "src/logbook/db.py"


@pytest.mark.asyncio
async def test_mcp_messaging_and_lease_signatures():
    """测试 MCP 工具函数签名与入参校验。"""
    # 1. 验证空参数阻断或协商响应结构
    err_send = await message_send(
        project="",
        from_agent="codebuddy",
        to_agent="zcode",
        subject="测试",
        content="内容"
    )
    assert err_send.get("isError") is True

    err_lease = await lease_acquire(
        project="",
        agent_name="codebuddy",
        file_path="src/db.py"
    )
    assert err_lease.get("isError") is True


def test_agent_message_ip_fields_and_validation():
    """测试 AgentMessageSend 与 AgentMessage 的 IP 字段默认值与边界校验。"""
    from pydantic import ValidationError

    # 1. 默认 IP 为 0.0.0.0
    msg = AgentMessageSend(
        project_id="logbook",
        from_agent="codebuddy",
        to_agent="zcode",
        subject="测试",
        content="内容"
    )
    assert msg.from_ip == "0.0.0.0"
    assert msg.to_ip == "0.0.0.0"

    # 2. 自定义 IPv4 与 IPv6 支持
    msg_custom = AgentMessageSend(
        project_id="logbook",
        from_agent="codebuddy",
        from_ip="192.168.1.100",
        to_agent="zcode",
        to_ip="2001:0db8:85a3:0000:0000:8a2e:0370:7334",
        subject="自定义IP",
        content="内容"
    )
    assert msg_custom.from_ip == "192.168.1.100"
    assert msg_custom.to_ip == "2001:0db8:85a3:0000:0000:8a2e:0370:7334"

    # 3. 超过 45 字符校验拒绝
    with pytest.raises(ValidationError):
        AgentMessageSend(
            project_id="logbook",
            from_agent="codebuddy",
            from_ip="a" * 46,
            to_agent="zcode",
            subject="超长IP",
            content="内容"
        )


def test_detect_node_ip():
    """测试 detect_node_ip 能够探测到有效的非空 IP 格式。"""
    from logbook.mcp_server import detect_node_ip
    ip = detect_node_ip()
    assert isinstance(ip, str)
    assert len(ip) > 0
    assert "." in ip or ":" in ip

