import os

import pytest
from unittest.mock import AsyncMock, patch

from logbook.db import db
from logbook.mcp_server import (
    get_agent_personal_inbox_resource,
    lease_acquire,
    lease_release,
    message_inbox,
    message_read,
    message_send,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_message_read_requires_and_enforces_recipient_identity(monkeypatch):
    await db.ensure_messaging_schema()
    sent = await db.send_agent_message(
        project_id="identity_probe",
        from_agent="alice",
        to_agent="bob",
        subject="authorization",
        content="private body",
    )

    monkeypatch.delenv("LOGBOOK_AGENT_ID", raising=False)
    missing_identity = await message_read(sent.id)
    assert missing_identity["isError"] is True
    assert missing_identity["error_type"] == "CALLER_IDENTITY_REQUIRED"

    monkeypatch.setenv("LOGBOOK_AGENT_ID", "eve")
    mismatch = await message_read(sent.id, agent_name="bob")
    assert mismatch["isError"] is True
    assert mismatch["error_type"] == "CALLER_IDENTITY_MISMATCH"

    denied = await message_read(sent.id)
    assert denied["isError"] is True
    assert denied["error_type"] == "MESSAGE_NOT_READABLE"
    assert "private body" not in str(denied)
    row = await db.get_agent_inbox("identity_probe", "bob", unread_only=False)
    assert row[0].is_read is False
    assert row[0].read_by_agent is None

    monkeypatch.setenv("LOGBOOK_AGENT_ID", "bob")
    allowed = await message_read(sent.id)
    assert allowed["ok"] is True
    assert allowed["message"]["content"] == "private body"
    assert allowed["message"]["read_by_agent"] == "bob"

    repeated = await message_read(sent.id)
    assert repeated["ok"] is True
    assert repeated["message"]["read_at"] == allowed["message"]["read_at"]


@pytest.mark.asyncio
async def test_mailbox_listing_and_send_enforce_trusted_identity(monkeypatch):
    await db.ensure_messaging_schema()
    sent = await db.send_agent_message(
        project_id="identity_probe",
        from_agent="alice",
        to_agent="bob",
        subject="listing authorization",
        content="private listing body",
    )

    monkeypatch.setenv("LOGBOOK_AGENT_ID", "eve")
    denied_inbox = await message_inbox(agent_name="bob", project=None, unread_only=False)
    assert denied_inbox["isError"] is True
    assert denied_inbox["error_type"] == "CALLER_IDENTITY_MISMATCH"
    assert "private listing body" not in str(denied_inbox)

    denied_resource = await get_agent_personal_inbox_resource("bob")
    assert "CALLER_IDENTITY_MISMATCH" in denied_resource
    assert "private listing body" not in denied_resource

    denied_send = await message_send(
        project="identity_probe",
        from_agent="bob",
        to_agent="alice",
        subject="spoof",
        content="spoofed body",
        allow_cross_project=True,
    )
    assert denied_send["isError"] is True
    assert denied_send["error_type"] == "CALLER_IDENTITY_MISMATCH"

    monkeypatch.setenv("LOGBOOK_AGENT_ID", "bob")
    allowed_inbox = await message_inbox(agent_name="bob", project=None, unread_only=False)
    assert allowed_inbox["ok"] is True
    assert any(item["id"] == sent.id for item in allowed_inbox["messages"])


@pytest.mark.asyncio
async def test_file_lease_tools_enforce_trusted_holder_identity(monkeypatch):
    await db.ensure_messaging_schema()
    with patch(
        "logbook.mcp_server.validate_and_negotiate_project",
        new=AsyncMock(return_value="lease_identity_probe"),
    ):
        monkeypatch.setenv("LOGBOOK_AGENT_ID", "holder")
        acquired = await lease_acquire(
            project="lease_identity_probe",
            agent_name="holder",
            file_path="src/protected.py",
            allow_cross_project=True,
        )
        assert acquired["ok"] is True

        monkeypatch.setenv("LOGBOOK_AGENT_ID", "eve")
        spoofed_renew = await lease_acquire(
            project="lease_identity_probe",
            agent_name="holder",
            file_path="src/protected.py",
            allow_cross_project=True,
        )
        assert spoofed_renew["isError"] is True
        assert spoofed_renew["error_type"] == "CALLER_IDENTITY_MISMATCH"

        spoofed_release = await lease_release(
            project="lease_identity_probe",
            agent_name="holder",
            file_path="src/protected.py",
            allow_cross_project=True,
        )
        assert spoofed_release["isError"] is True
        assert spoofed_release["error_type"] == "CALLER_IDENTITY_MISMATCH"

        monkeypatch.setenv("LOGBOOK_AGENT_ID", "holder")
        released = await lease_release(
            project="lease_identity_probe",
            agent_name="holder",
            file_path="src/protected.py",
            allow_cross_project=True,
        )
        assert released["released"] is True
