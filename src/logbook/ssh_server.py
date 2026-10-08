"""Logbook 高性能 AsyncSSH 服务端

专为 Agent 远程调用设计:
- 0 APT / 0 外部依赖: 纯 Python 异步事件循环驱动
- 身份认证: 支持 sysadmin 公钥免密 (兼容 ~/.ssh/id_ed25519) 及默认密码
- 管道代理:
  - `mcp` / `logbook mcp`: 自动拉起 FastMCP Stdio 管道并全双工桥接 JSON-RPC 2.0
  - `logbook <args>`: 自动执行 CLI 命令并返回 ANSI/文本流
"""

import asyncio
import logging
import os
import shlex
import sys
from pathlib import Path
from typing import Optional
import asyncssh

logger = logging.getLogger("logbook.ssh")

AUTHORIZED_KEYS_PATH = os.getenv("SSH_AUTHORIZED_KEYS_PATH", "/home/sysadmin/.ssh/authorized_keys")
DEFAULT_USER = os.getenv("SSH_USER", "sysadmin")
DEFAULT_PASSWORD = os.getenv("SSH_PASSWORD", "logbook")
HOST_KEY_PATH = os.getenv("SSH_HOST_KEY_PATH", "./data/host_key")


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
        return True

    async def validate_password(self, username: str, password: str) -> bool:
        if username == DEFAULT_USER and password == DEFAULT_PASSWORD:
            logger.info(f"Password authentication succeeded for '{username}'")
            return True
        logger.warning(f"Password authentication failed for '{username}'")
        return False

    def public_key_auth_supported(self) -> bool:
        return True

    async def validate_public_key(self, username: str, key: asyncssh.SSHKey) -> bool:
        if username != DEFAULT_USER:
            return False

        # 1. 检查环境变量提供的 authorized keys
        env_keys = os.getenv("SSH_AUTHORIZED_KEYS", "")
        # 2. 检查文件中的 authorized keys
        file_keys = ""
        p = Path(AUTHORIZED_KEYS_PATH)
        if p.exists():
            try:
                file_keys = p.read_text(encoding="utf-8")
            except Exception:
                pass

        all_keys_text = f"{env_keys}\n{file_keys}"
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


async def pipe_streams(reader, writer):
    """双向流管道转发生命周期"""
    try:
        while not reader.at_eof():
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
        pass
    except Exception as e:
        logger.debug(f"Stream pipe notice: {e}")
    finally:
        try:
            if hasattr(writer, "write_eof"):
                writer.write_eof()
            elif hasattr(writer, "close"):
                writer.close()
        except Exception:
            pass


async def handle_ssh_process(process: asyncssh.SSHServerProcess) -> None:
    """处理 SSH 子进程请求 (MCP 协议与 CLI 指令)"""
    raw_cmd = (process.command or "").strip()
    username = process.get_extra_info("username")
    logger.info(f"Executing SSH command '{raw_cmd}' for user '{username}'")

    if not raw_cmd or raw_cmd in ("mcp", "logbook mcp", "logbook-mcp"):
        # 1. AI Agent Native MCP 通道 (启动 python -m logbook.cli mcp 并双向直通)
        cmd_args = [sys.executable, "-m", "logbook.cli", "mcp"]
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

    # 派生子进程
    try:
        subproc = await asyncio.create_subprocess_exec(
            *cmd_args,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=os.environ.copy()
        )
    except Exception as e:
        process.stderr.write(f"Failed to spawn Logbook process: {e}\r\n")
        process.exit(1)
        return

    # 全双工管道泵
    t1 = asyncio.create_task(pipe_streams(process.stdin, subproc.stdin))
    t2 = asyncio.create_task(pipe_streams(subproc.stdout, process.stdout))
    t3 = asyncio.create_task(pipe_streams(subproc.stderr, process.stderr))

    rc = 1
    try:
        # 等待子进程退出
        rc = await subproc.wait()
    except (asyncio.CancelledError, Exception):
        rc = 1
    finally:
        # 严格清理孤儿进程与后台任务，杜绝内存泄漏
        t1.cancel()
        t2.cancel()
        t3.cancel()
        if subproc.returncode is None:
            try:
                subproc.terminate()
                try:
                    await asyncio.wait_for(subproc.wait(), timeout=1.0)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    subproc.kill()
            except Exception:
                pass
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


async def start_ssh_server(host: str = "0.0.0.0", port: int = 22):
    """启动 Logbook SSH 服务端"""
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
    port = int(os.getenv("SSH_PORT", "22"))
    host = os.getenv("SSH_HOST", "0.0.0.0")
    server = await start_ssh_server(host, port)
    logger.info(f"Logbook Server Ready on {host}:{port}")
    await server.wait_closed()


if __name__ == "__main__":
    asyncio.run(run_server())
