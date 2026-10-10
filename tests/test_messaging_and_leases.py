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


@pytest.mark.asyncio
async def test_personal_mailbox_resources(monkeypatch):
    """测试个人信箱 MCP 资源挂载直读函数 (logbook://mailbox/{agent}/inbox 等)。"""
    from logbook.mcp_server import (
        get_agent_personal_inbox_resource,
        get_agent_project_mailbox_resource,
        db
    )
    from logbook.time_sync import get_beijing_now

    mock_msgs = [
        AgentMessage(
            id=101,
            project_id="brix",
            from_agent="zcode",
            from_ip="192.168.1.22",
            to_agent="agy",
            to_ip="192.168.1.30",
            subject="联调对讲测试",
            content="这是一封测试信件",
            task_id="L-MSG-1",
            thread_id=None,
            is_read=False,
            read_at=None,
            created_at=get_beijing_now(),
        )
    ]

    async def mock_get_agent_inbox(project_id, agent_name, unread_only=True, limit=20):
        return mock_msgs

    monkeypatch.setattr(db, "get_agent_inbox", mock_get_agent_inbox)

    # 1. 验证全局个人信箱资源返回包含真实局域网 IP
    inbox_md = await get_agent_personal_inbox_resource("agy")
    assert "Agent [agy] 个人信箱" in inbox_md
    assert "192.168.1.22" in inbox_md
    assert "101" in inbox_md
    assert "联调对讲测试" in inbox_md

    # 2. 验证项目信箱资源返回
    async def mock_val_proj(proj, is_write=False):
        return proj
    monkeypatch.setattr("logbook.mcp_server.validate_and_negotiate_project", mock_val_proj)

    proj_md = await get_agent_project_mailbox_resource("brix", "agy")
    assert "项目 [brix] Agent [agy] 专属信箱" in proj_md
    assert "192.168.1.22" in proj_md
    assert "101" in proj_md


def test_db_schema_ensured_flag():
    """测试 Database 类的 _messaging_schema_ensured 与 _researches_schema_ensured 初始状态。"""
    from logbook.db import Database
    test_db = Database()
    assert test_db._messaging_schema_ensured is False
    assert test_db._researches_schema_ensured is False


