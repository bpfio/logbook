import pytest
import asyncssh

from logbook import ssh_server


def test_password_authentication_is_disabled_by_default(monkeypatch):
    monkeypatch.delenv("SSH_ENABLE_PASSWORD_AUTH", raising=False)
    assert ssh_server.password_auth_enabled() is False


def test_startup_rejects_missing_or_weak_password(monkeypatch, tmp_path):
    monkeypatch.setattr(ssh_server, "password_auth_enabled", lambda: True)
    monkeypatch.setattr(ssh_server, "AUTHORIZED_KEYS_PATH", str(tmp_path / "missing"))
    monkeypatch.setattr(ssh_server, "AGENT_KEYS_ROOT", tmp_path / "agent_keys")
    monkeypatch.delenv("SSH_AUTHORIZED_KEYS", raising=False)

    for password in (None, "", "logbook", "short-password"):
        if password is None:
            monkeypatch.delenv("SSH_PASSWORD", raising=False)
        else:
            monkeypatch.setenv("SSH_PASSWORD", password)
        with pytest.raises(RuntimeError, match="SSH_PASSWORD"):
            ssh_server.validate_ssh_authentication()


@pytest.mark.asyncio
async def test_strong_password_is_accepted_only_when_explicitly_enabled(monkeypatch):
    monkeypatch.setattr(ssh_server, "password_auth_enabled", lambda: True)
    monkeypatch.setenv("SSH_PASSWORD", "a-strong-test-password-2026")
    server = ssh_server.LogbookSSHServer()
    assert await server.validate_password("sysadmin", "a-strong-test-password-2026") is True
    assert await server.validate_password("sysadmin", "logbook") is False

    monkeypatch.setattr(ssh_server, "password_auth_enabled", lambda: False)
    assert await server.validate_password("sysadmin", "a-strong-test-password-2026") is False


def test_startup_accepts_configured_public_key(monkeypatch, tmp_path):
    key = asyncssh.generate_private_key("ssh-ed25519")
    public_key = key.export_public_key().decode("ascii").strip()
    monkeypatch.setattr(ssh_server, "password_auth_enabled", lambda: False)
    monkeypatch.setattr(ssh_server, "AUTHORIZED_KEYS_PATH", str(tmp_path / "missing"))
    monkeypatch.setattr(ssh_server, "AGENT_KEYS_ROOT", tmp_path / "agent_keys")
    monkeypatch.setenv("SSH_AUTHORIZED_KEYS", public_key)
    ssh_server.validate_ssh_authentication()


def test_ssh_principal_resolves_to_trusted_agent_identity(monkeypatch):
    monkeypatch.setenv("LOGBOOK_DEFAULT_AGENT_ID", "agy")
    assert ssh_server.resolve_agent_identity("sysadmin") == "agy"
    assert ssh_server.resolve_agent_identity("zcode") == "zcode"
    with pytest.raises(RuntimeError, match="invalid SSH agent identity"):
        ssh_server.resolve_agent_identity("../escape")
