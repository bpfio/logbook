import asyncio
import json
import os
import pathlib
import shlex
import sys
import time

import asyncssh
import pytest

from logbook.ssh_server import mcp_bridge_streams, pipe_streams
from logbook import ssh_server


class QueueReader:
    def __init__(self, lines):
        self.lines = list(lines)

    async def read(self, size):
        if not self.lines:
            return b""
        return self.lines.pop(0)

    async def readline(self):
        if not self.lines:
            return b""
        return self.lines.pop(0)


class AsyncQueueReader:
    def __init__(self, queue):
        self.queue = queue

    async def readline(self):
        return await asyncio.wait_for(self.queue.get(), timeout=2.0)


class Writer:
    def __init__(self, stdout_queue=None, response_delay=0.0):
        self.data = b""
        self.eof = False
        self.stdout_queue = stdout_queue
        self.response_delay = response_delay

    def write(self, data):
        self.data += data

    async def drain(self):
        if self.response_delay and self.stdout_queue is not None:
            await asyncio.sleep(self.response_delay)
            self.stdout_queue.put_nowait(
                json.dumps({"jsonrpc": "2.0", "id": 7, "result": {"ok": True}}).encode() + b"\n"
            )
            self.stdout_queue.put_nowait(b"")

    def write_eof(self):
        self.eof = True


async def test_pipe_streams_does_not_use_fixed_sleep():
    reader = QueueReader([])
    writer = Writer()
    started = time.monotonic()
    await pipe_streams(reader, writer)
    assert time.monotonic() - started < 0.2
    assert writer.eof is True


async def test_mcp_bridge_waits_for_slow_response_before_child_eof():
    request = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call"}).encode() + b"\n"
    client_reader = QueueReader([request, b""])
    child_stdout_lines = asyncio.Queue()
    child_writer = Writer(stdout_queue=child_stdout_lines, response_delay=0.8)
    client_writer = Writer()

    started = time.monotonic()
    await mcp_bridge_streams(
        client_reader,
        child_writer,
        AsyncQueueReader(child_stdout_lines),
        client_writer,
    )
    elapsed = time.monotonic() - started

    assert elapsed >= 0.7
    assert child_writer.eof is True
    assert client_writer.eof is True
    assert b'"id": 7' in client_writer.data


@pytest.mark.asyncio
async def test_real_ssh_slow_mcp_response_survives_client_stdin_eof(monkeypatch, tmp_path):
    client_key = asyncssh.generate_private_key("ssh-ed25519")
    authorized_key = client_key.export_public_key().decode("ascii").strip()
    test_server = tmp_path / "slow_mcp_server.py"
    test_server.write_text(
        (pathlib.Path(__file__).parent / "slow_mcp_server.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    monkeypatch.setenv("SSH_ENABLE_PASSWORD_AUTH", "0")
    monkeypatch.setenv("SSH_AUTHORIZED_KEYS", authorized_key)
    monkeypatch.setenv("SLOW_MCP_DELAY", "5.2")
    pid_file = tmp_path / "slow_mcp.pid"
    monkeypatch.setenv("SLOW_MCP_PID_FILE", str(pid_file))
    monkeypatch.setenv(
        "LOGBOOK_MCP_SERVER_COMMAND",
        f"{shlex.quote(sys.executable)} {shlex.quote(str(test_server))}",
    )
    monkeypatch.setattr(ssh_server, "HOST_KEY_PATH", str(tmp_path / "host_key"))
    monkeypatch.setattr(ssh_server, "AUTHORIZED_KEYS_PATH", str(tmp_path / "missing"))
    monkeypatch.setattr(ssh_server, "AGENT_KEYS_ROOT", tmp_path / "agent_keys")

    server = await ssh_server.start_ssh_server(host="127.0.0.1", port=0)
    try:
        request = {
            "jsonrpc": "2.0",
            "id": 41,
            "method": "tools/call",
            "params": {"name": "task_query", "arguments": {}},
        }
        started = time.monotonic()
        async with asyncssh.connect(
            "127.0.0.1",
            port=server.get_port(),
            username="sysadmin",
            client_keys=[client_key],
            known_hosts=None,
        ) as connection:
            stdin, stdout, stderr = await connection.open_session(command="mcp")
            try:
                stdin.write(json.dumps(request) + "\n")
                stdin.write_eof()
                response_line = await asyncio.wait_for(stdout.readline(), timeout=8.0)
                response = json.loads(response_line)
                elapsed = time.monotonic() - started
                assert response["id"] == 41
                assert response["result"]["slow"] is True
                assert elapsed >= 5.0
                child_pid = int(pid_file.read_text(encoding="ascii"))
                deadline = time.monotonic() + 2.0
                while True:
                    try:
                        os.kill(child_pid, 0)
                    except ProcessLookupError:
                        break
                    if time.monotonic() >= deadline:
                        raise AssertionError("slow MCP child process remained alive")
                    await asyncio.sleep(0.05)
            finally:
                stdin.close()
    finally:
        server.close()
        await server.wait_closed()
