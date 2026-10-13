"""Logbook 高性能 AsyncSSH 服务端

专为 Agent 远程调用设计:
- 0 APT / 0 外部依赖: 纯 Python 异步事件循环驱动
- 身份认证: 支持 sysadmin 公钥免密 (兼容 ~/.ssh/id_ed25519) 及默认密码
- 管道代理:
  - `mcp` / `logbook mcp`: 自动拉起 FastMCP Stdio 管道并全双工桥接 JSON-RPC 2.0
  - `logbook <args>`: 自动执行 CLI 命令并返回 ANSI/文本流
"""

import asyncio
import json
import logging
import os
import re
import shlex
import sys
from pathlib import Path
from typing import Optional
import asyncssh

from logbook.migrate import up_with_retry

logger = logging.getLogger("logbook.ssh")

AUTHORIZED_KEYS_PATH = os.getenv("SSH_AUTHORIZED_KEYS_PATH", "/home/sysadmin/.ssh/authorized_keys")
DEFAULT_USER = os.getenv("SSH_USER", "sysadmin")
AGENT_KEYS_ROOT = Path(
    os.getenv("SSH_AGENT_KEYS_ROOT", "/app/data/agent_keys")
).expanduser()
HOST_KEY_PATH = os.getenv("SSH_HOST_KEY_PATH", "./data/host_key")
WEAK_PASSWORDS = {"", "logbook", "password", "admin", "logbook_dev_secret"}
PRINCIPAL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
AGENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


def _env_enabled(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def password_auth_enabled() -> bool:
    return _env_enabled("SSH_ENABLE_PASSWORD_AUTH", default=False)


def _strong_password(password: str | None) -> bool:
    if password is None:
        return False
    normalized = password.strip()
    return len(normalized) >= 16 and normalized not in WEAK_PASSWORDS


def _valid_key_lines(text: str) -> list[str]:
    valid: list[str] = []
    for line in text.splitlines():
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        try:
            asyncssh.import_public_key(candidate)
        except Exception:
            continue
        valid.append(candidate)
    return valid


def _default_user_key_text() -> str:
    env_keys = os.getenv("SSH_AUTHORIZED_KEYS", "")
    file_keys = ""
    path = Path(AUTHORIZED_KEYS_PATH)
    if path.exists():
        try:
            file_keys = path.read_text(encoding="utf-8")
        except Exception:
            file_keys = ""
    return f"{env_keys}\n{file_keys}"


def _agent_principal_key_text(username: str) -> str:
    if not PRINCIPAL_RE.fullmatch(username):
        return ""
    key_path = AGENT_KEYS_ROOT / username / "authorized_keys"
    if not key_path.exists():
        return ""
    try:
        return key_path.read_text(encoding="utf-8")
    except Exception:
        return ""


def resolve_agent_identity(username: str | None) -> str:
    principal = username or ""
    if principal == DEFAULT_USER:
        identity = os.getenv("LOGBOOK_DEFAULT_AGENT_ID", "agy").strip()
    else:
        identity = principal.strip()
    if not AGENT_ID_RE.fullmatch(identity):
        raise RuntimeError(f"invalid SSH agent identity: {identity!r}")
    return identity


def validate_ssh_authentication() -> None:
    if password_auth_enabled():
        if not _strong_password(os.getenv("SSH_PASSWORD")):
            raise RuntimeError(
                "SSH password authentication is enabled but SSH_PASSWORD is missing, weak, or a known default"
            )
        return

    key_sources = [_default_user_key_text()]
    if AGENT_KEYS_ROOT.exists():
        key_sources.extend(
            _agent_principal_key_text(path.name)
            for path in AGENT_KEYS_ROOT.iterdir()
            if path.is_dir()
        )
    if not any(_valid_key_lines(text) for text in key_sources):
        raise RuntimeError(
            "SSH password authentication is disabled and no valid authorized public key is configured"
        )


class LogbookSSHServer(asyncssh.SSHServer):
    """Logbook 专用 AsyncSSH 鉴权服务"""

    def connection_made(self, conn: asyncssh.SSHServerConnection) -> None:
        peer = conn.get_extra_info("peername")
        logger.info(f"Incoming SSH connection from {peer}")
        sock = conn.get_extra_info("socket")
        if sock:
            try:
                import socket
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except Exception:
                pass

    def connection_lost(self, exc: Optional[Exception]) -> None:
        if exc:
            logger.debug(f"SSH connection lost: {exc}")
        else:
            logger.info("SSH connection closed normally.")

    def begin_auth(self, username: str) -> bool:
        return True

    def password_auth_supported(self) -> bool:
        return password_auth_enabled()

    async def validate_password(self, username: str, password: str) -> bool:
        if (
            password_auth_enabled()
            and username == DEFAULT_USER
            and _strong_password(password)
        ):
            logger.info(f"Password authentication succeeded for '{username}'")
            return True
        logger.warning(f"Password authentication failed for '{username}'")
        return False

    def public_key_auth_supported(self) -> bool:
        return True

    async def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
        if username != DEFAULT_USER and not PRINCIPAL_RE.fullmatch(username):
            return False

        all_keys_text = (
            _default_user_key_text()
            if username == DEFAULT_USER
            else _agent_principal_key_text(username)
        )
        for line in all_keys_text.splitlines():
            clean = line.strip()
            if not clean or clean.startswith("#"):
                continue
            try:
                imported = asyncssh.import_public_key(clean)
                if key == imported:
                    logger.info(f"Public key authentication succeeded for '{username}'")
                    return True
            except Exception:
                continue

        logger.warning(f"Public key authentication failed for '{username}'")
        return False


def _finish_writer(writer) -> None:
    try:
        if hasattr(writer, "write_eof"):
            writer.write_eof()
        elif hasattr(writer, "close"):
            writer.close()
    except Exception:
        pass


async def pipe_streams(reader, writer, propagate_eof: bool = True):
    """Forward one direction without guessing protocol completion."""
    completed = False
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
        completed = True
    except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
        pass
    except Exception as e:
        logger.debug(f"Stream pipe notice: {e}")
    finally:
        if completed and propagate_eof:
            _finish_writer(writer)


def _jsonrpc_frame(line: bytes) -> tuple[str, object] | None:
    try:
        message = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(message, dict) or "id" not in message:
        return None
    if "method" in message:
        return "request", message["id"]
    if "result" in message or "error" in message:
        return "response", message["id"]
    return None


async def mcp_bridge_streams(
    client_reader,
    child_writer,
    child_stdout,
    client_writer,
) -> None:
    """Bridge MCP stdio while deferring child EOF until all request IDs settle.

    Only JSON-RPC envelope IDs are inspected; tool names and business payloads
    are forwarded verbatim.
    """
    pending: set[object] = set()
    input_eof = False
    output_eof = False
    child_stdin_closed = False

    def close_child_stdin() -> None:
        nonlocal child_stdin_closed
        if child_stdin_closed:
            return
        child_stdin_closed = True
        _finish_writer(child_writer)

    def maybe_close_child_stdin() -> None:
        if input_eof and not pending:
            close_child_stdin()

    async def input_loop() -> None:
        nonlocal input_eof
        try:
            while True:
                line = await client_reader.readline()
                if not line:
                    input_eof = True
                    maybe_close_child_stdin()
                    return
                frame = _jsonrpc_frame(line)
                if frame and frame[0] == "request":
                    pending.add(frame[1])
                child_writer.write(line)
                await child_writer.drain()
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            input_eof = True
            maybe_close_child_stdin()
        except Exception as exc:
            logger.debug(f"MCP stdin bridge notice: {exc}")
            input_eof = True
            maybe_close_child_stdin()

    async def output_loop() -> None:
        nonlocal output_eof
        try:
            while True:
                line = await child_stdout.readline()
                if not line:
                    output_eof = True
                    maybe_close_child_stdin()
                    _finish_writer(client_writer)
                    return
                client_writer.write(line)
                await client_writer.drain()
                frame = _jsonrpc_frame(line)
                if frame and frame[0] == "response":
                    pending.discard(frame[1])
                    maybe_close_child_stdin()
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            output_eof = True
        except Exception as exc:
            logger.debug(f"MCP stdout bridge notice: {exc}")
            output_eof = True

    input_task = asyncio.create_task(input_loop())
    output_task = asyncio.create_task(output_loop())
    try:
        await asyncio.gather(input_task, output_task)
    finally:
        for task in (input_task, output_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(input_task, output_task, return_exceptions=True)


async def handle_ssh_process(process: asyncssh.SSHServerProcess) -> None:
    """处理 SSH 子进程请求 (MCP 协议与 CLI 指令)"""
    raw_cmd = (process.command or "").strip()
    username = process.get_extra_info("username")
    logger.info(f"Executing SSH command '{raw_cmd}' for user '{username}'")

    mcp_mode = not raw_cmd or raw_cmd in ("mcp", "logbook mcp", "logbook-mcp")
    if mcp_mode:
        # 1. AI Agent Native MCP 通道 (启动 python -m logbook.cli mcp 并双向直通)
        override = os.getenv("LOGBOOK_MCP_SERVER_COMMAND", "").strip()
        cmd_args = shlex.split(override) if override else [sys.executable, "-m", "logbook.cli", "mcp"]
    else:
        # 2. CLI 命令执行
        tokens = shlex.split(raw_cmd)
        if tokens and tokens[0] == "logbook":
            cmd_args = [sys.executable, "-m", "logbook.cli"] + tokens[1:]
        elif tokens and tokens[0] in ("doctor", "status", "show", "import", "export"):
            cmd_args = [sys.executable, "-m", "logbook.cli"] + tokens
        else:
            process.stderr.write(
                f"Logbook SSH: Command '{raw_cmd}' is not recognized.\r\n"
                f"Supported commands: 'mcp' (JSON-RPC), 'logbook doctor', 'logbook status <project>', etc.\r\n"
            )
            process.exit(1)
            return

    # 派生子进程 (透传 SSH 客户端连接信息环境变量，使下游 MCP 引擎可原生自动探测调用端真实 IP)
    env = os.environ.copy()
    peer = process.get_extra_info("peername")
    if not peer:
        conn = process.get_extra_info("connection")
        if conn:
            peer = conn.get_extra_info("peername")
    sock = process.get_extra_info("sockname")
    if not sock:
        conn = process.get_extra_info("connection")
        if conn:
            sock = conn.get_extra_info("sockname")

    if peer and isinstance(peer, (tuple, list)) and len(peer) >= 2:
        client_ip = str(peer[0])
        client_port = str(peer[1])
        server_ip = str(sock[0]) if sock and isinstance(sock, (tuple, list)) and len(sock) >= 1 else "0.0.0.0"
        server_port = str(sock[1]) if sock and isinstance(sock, (tuple, list)) and len(sock) >= 2 else "22"
        env["SSH_CLIENT"] = f"{client_ip} {client_port} {server_port}"
        env["SSH_CONNECTION"] = f"{client_ip} {client_port} {server_ip} {server_port}"
        logger.info(f"Injected SSH_CLIENT='{env['SSH_CLIENT']}' for user '{username}'")

    env["LOGBOOK_SSH_PRINCIPAL"] = username or ""
    env["LOGBOOK_AGENT_ID"] = resolve_agent_identity(username)

    try:
        subproc = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env
        )
    except Exception as e:
        process.stderr.write(f"Failed to spawn Logbook process: {e}\r\n")
        process.exit(1)
        return

    # MCP keeps stdin open until every request ID has received a response.
    if mcp_mode:
        pipe_tasks = [
            asyncio.create_task(
                mcp_bridge_streams(
                    process.stdin,
                    subproc.stdin,
                    subproc.stdout,
                    process.stdout,
                )
            ),
            asyncio.create_task(pipe_streams(subproc.stderr, process.stderr)),
        ]
    else:
        pipe_tasks = [
            asyncio.create_task(pipe_streams(process.stdin, subproc.stdin)),
            asyncio.create_task(pipe_streams(subproc.stdout, process.stdout)),
            asyncio.create_task(pipe_streams(subproc.stderr, process.stderr)),
        ]

    rc = 1
    wait_task = asyncio.create_task(subproc.wait())
    client_closed_task = None
    if hasattr(process, "wait_closed"):
        client_closed_task = asyncio.create_task(process.wait_closed())
    try:
        waiters = {wait_task}
        if client_closed_task is not None:
            waiters.add(client_closed_task)
        done, _ = await asyncio.wait(waiters, return_when=asyncio.FIRST_COMPLETED)
        if client_closed_task in done and subproc.returncode is None:
            for task in pipe_tasks:
                task.cancel()
            await asyncio.gather(*pipe_tasks, return_exceptions=True)
            subproc.terminate()
        if wait_task in done:
            rc = wait_task.result()
        else:
            rc = await wait_task
    except (asyncio.CancelledError, Exception):
        rc = 1
    finally:
        for task in pipe_tasks:
            task.cancel()
        await asyncio.gather(*pipe_tasks, return_exceptions=True)
        if client_closed_task is not None:
            client_closed_task.cancel()
            await asyncio.gather(client_closed_task, return_exceptions=True)
        if subproc.returncode is None:
            try:
                subproc.terminate()
                try:
                    await asyncio.wait_for(subproc.wait(), timeout=1.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    subproc.kill()
            except Exception:
                pass
        if not wait_task.done():
            await asyncio.gather(wait_task, return_exceptions=True)
        process.exit(rc if rc is not None else 0)


def ensure_host_key(key_path_str: str) -> str:
    """确保 SSH Host Key 存在"""
    key_path = Path(key_path_str)
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if not key_path.exists():
        logger.info(f"Generating new SSH ed25519 host key at {key_path}...")
        key = asyncssh.generate_private_key("ssh-ed25519")
        key.write_private_key(str(key_path))
    return str(key_path)


async def start_ssh_server(host: str = "127.0.0.1", port: int = 22):
    """启动 Logbook SSH 服务端"""
    validate_ssh_authentication()
    host_key = ensure_host_key(HOST_KEY_PATH)
    logger.info(f"Starting Logbook SSH Server on {host}:{port}...")
    server = await asyncssh.create_server(
        LogbookSSHServer,
        host=host,
        port=port,
        server_host_keys=[host_key],
        process_factory=handle_ssh_process,
        encoding=None,  # 二进制透明管道
        login_timeout=30.0,
        keepalive_interval=30.0,
        keepalive_count_max=3,
    )
    logger.info(f"Logbook SSH Server listening on {host}:{port}")
    return server


async def run_server():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    if os.getenv("LOGBOOK_AUTO_MIGRATE", "1") != "0":
        logger.info("Verifying database migrations before binding SSH...")
        migration_result = await up_with_retry()
        logger.info(
            "Database migrations ready: adopted=%s applied=%s",
            migration_result.get("adopted"),
            migration_result.get("applied"),
        )
    port = int(os.getenv("SSH_PORT", "22"))
    host = os.getenv("SSH_HOST", "127.0.0.1")
    server = await start_ssh_server(host, port)
    logger.info(f"Logbook Server Ready on {host}:{port}")
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(run_server())
