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
            logger.error(f"MCP stdout bridge exception: {exc}", exc_info=True)
            output_eof = True
            maybe_close_child_stdin()
            _finish_writer(client_writer)

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
        elif tokens and tokens[0] == "echo":
            # 允许 echo 探针命令直接回显，消除自动化测试或外部 Agent 裸连探测误判
            out = (" ".join(tokens[1:]) if len(tokens) > 1 else "") + "\r\n"
            process.stdout.write(out.encode("utf-8"))
            process.exit(0)
            return
        elif tokens and tokens[0] in ("ping", "test"):
            # 允许 ping 探针命令直接响应
            process.stdout.write(b"pong\r\n")
            process.exit(0)
            return
        else:
            msg = (
                f"Logbook SSH: Command '{raw_cmd}' is not recognized.\r\n"
                f"Supported commands: 'mcp' (JSON-RPC), 'logbook doctor', 'logbook status <project>', 'echo <text>', 'ping', etc.\r\n"
            )
            process.stderr.write(msg.encode("utf-8"))
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
            limit=4 * 1024 * 1024,
            env=env
        )
    except Exception as e:
        process.stderr.write(f"Failed to spawn Logbook process: {e}\r\n".encode("utf-8"))
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
    done = set()
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
            rc = 0
        elif wait_task in done:
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
        if client_closed_task is not None and client_closed_task in done:
            rc = 0
        elif isinstance(rc, int) and rc < 0:
            rc = 128 + abs(rc)
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


async def start_sse_server(host: str = "0.0.0.0", port: int = 8000, server_holder: dict | None = None):
    """启动 FastMCP SSE HTTP 服务端 (支持 /sse、/rpc 与 /health 探针，OPT-04 & OPT-08)"""
    import uvicorn
    from starlette.responses import JSONResponse
    from starlette.routing import Route
    from logbook.mcp_server import mcp, _session_bootstrap, _SERVER_VERSION

    if not os.getenv("LOGBOOK_AGENT_ID"):
        os.environ["LOGBOOK_AGENT_ID"] = os.getenv("LOGBOOK_DEFAULT_AGENT_ID", "agy")

    try:
        await _session_bootstrap()
    except Exception as e:
        logger.warning(f"SSE session bootstrap notice: {e}")

    app = mcp.sse_app(
        sse_path="/sse",
        message_path="/messages/",
        host=host,
    )

    ssl_cert = os.getenv("LOGBOOK_SSL_CERTFILE")
    ssl_key = os.getenv("LOGBOOK_SSL_KEYFILE")
    scheme = "https" if (ssl_cert and ssl_key and os.path.exists(ssl_cert) and os.path.exists(ssl_key)) else "http"

    async def health_endpoint(request):
        return JSONResponse({
            "status": "ok",
            "service": "logbook",
            "version": _SERVER_VERSION,
            "transport": "sse",
            "scheme": scheme,
            "sse_endpoint": "/sse",
            "rpc_endpoint": "/rpc",
            "messages_endpoint": "/messages/",
        })

    async def rpc_endpoint(request):
        """无状态单次 JSON-RPC 2.0 / 直接调用端点 (OPT-04 & OPT-20 标准 MCP 生命周期与资源路由)"""
        try:
            body = await request.json()
        except Exception as e:
            return JSONResponse({"jsonrpc": "2.0", "error": {"code": -32700, "message": f"Parse error: {e}"}}, status_code=400)

        # 客户端 IP 探测 (X-Forwarded-For / request.client.host)
        client_ip = None
        if request.headers.get("x-forwarded-for"):
            client_ip = request.headers.get("x-forwarded-for").split(",")[0].strip()
        elif request.client and request.client.host:
            client_ip = request.client.host
        if client_ip:
            os.environ["HTTP_CLIENT_IP"] = client_ip

        req_id = body.get("id", 1)
        req_method = body.get("method")

        # 1. 标准 MCP 2024-11-05 协议生命周期握手 (OPT-20)
        if req_method == "initialize":
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {
                        "tools": {},
                        "resources": {},
                        "prompts": {},
                    },
                    "serverInfo": {
                        "name": "logbook",
                        "version": _SERVER_VERSION,
                    }
                }
            })
        elif req_method in ("notifications/initialized", "ping"):
            return JSONResponse({"jsonrpc": "2.0", "id": req_id, "result": {}})

        # 2. Tools 路由
        elif req_method == "tools/list":
            tools = await mcp.list_tools()
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"tools": [t.model_dump(mode="json") if hasattr(t, "model_dump") else t for t in tools]}
            })
        elif req_method == "tools/call":
            params = body.get("params") or {}
            tool_name = params.get("name")
            arguments = params.get("arguments") or {}
        elif "name" in body:
            tool_name = body.get("name")
            arguments = body.get("arguments") or {}

        # 3. Resources 路由 (OPT-20)
        elif req_method == "resources/list":
            resources = await mcp.list_resources() if hasattr(mcp, "list_resources") else []
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"resources": [r.model_dump(mode="json") if hasattr(r, "model_dump") else r for r in resources]}
            })
        elif req_method == "resources/read":
            params = body.get("params") or {}
            uri = params.get("uri")
            if not uri:
                return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Missing uri"}}, status_code=400)
            try:
                res_content = await mcp.read_resource(uri) if hasattr(mcp, "read_resource") else ""
                text_content = getattr(res_content, "text", str(res_content)) if hasattr(res_content, "text") else str(res_content)
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": text_content}]}
                })
            except Exception as e:
                return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(e)}}, status_code=500)

        # 4. Prompts 路由 (OPT-20)
        elif req_method == "prompts/list":
            prompts = await mcp.list_prompts() if hasattr(mcp, "list_prompts") else []
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"prompts": [p.model_dump(mode="json") if hasattr(p, "model_dump") else p for p in prompts]}
            })
        elif req_method == "prompts/get":
            params = body.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            if not name:
                return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32602, "message": "Missing prompt name"}}, status_code=400)
            try:
                prompt_res = await mcp.get_prompt(name, args) if hasattr(mcp, "get_prompt") else None
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": prompt_res.model_dump(mode="json") if hasattr(prompt_res, "model_dump") else {"messages": []}
                })
            except Exception as e:
                return JSONResponse({"jsonrpc": "2.0", "id": req_id, "error": {"code": -32000, "message": str(e)}}, status_code=500)
        else:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": f"Method not supported: {req_method}"}
            }, status_code=400)

        if not tool_name:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32602, "message": "Missing tool name"}
            }, status_code=400)

        try:
            res = await mcp.call_tool(tool_name, arguments)
            content_list = []
            if hasattr(res, "content") and res.content:
                for c in res.content:
                    txt = getattr(c, "text", str(c))
                    content_list.append({"type": "text", "text": txt})
            elif isinstance(res, dict):
                import json
                content_list.append({"type": "text", "text": json.dumps(res, ensure_ascii=False)})
            elif isinstance(res, str):
                content_list.append({"type": "text", "text": res})
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": content_list, "isError": getattr(res, "is_error", False)}
            })
        except Exception as e:
            return JSONResponse({
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32000, "message": str(e)}
            }, status_code=500)

    app.routes.append(Route("/health", health_endpoint))
    app.routes.append(Route("/ping", health_endpoint))
    app.routes.append(Route("/rpc", rpc_endpoint, methods=["POST"]))
    app.routes.append(Route("/call", rpc_endpoint, methods=["POST"]))

    config_kwargs = {
        "host": host,
        "port": port,
        "log_level": "info",
        "access_log": False,
    }
    if scheme == "https":
        config_kwargs["ssl_certfile"] = ssl_cert
        config_kwargs["ssl_keyfile"] = ssl_key

    config = uvicorn.Config(app, **config_kwargs)
    server = uvicorn.Server(config)
    if server_holder is not None:
        server_holder["server"] = server

    logger.info(f"Logbook FastMCP SSE Server listening on {scheme}://{host}:{port}/sse (health: /health, rpc: /rpc)")
    await server.serve()


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

    # 并行拉起 FastMCP 原生 SSE HTTP 服务 (默认仅回环 127.0.0.1 监听，消除局域网裸露隐患 F-NET-8000)
    sse_enabled = _env_enabled("LOGBOOK_ENABLE_SSE", default=False)
    sse_port = int(os.getenv("LOGBOOK_SSE_PORT", "8000"))
    sse_host = os.getenv("LOGBOOK_SSE_HOST", "127.0.0.1")

    # 启动后台预热任务 (消除客户端首笔请求的冷启动握手尖峰 F-SEARCH-05)
    from logbook.mcp_server import warmup_embedding_probe
    asyncio.create_task(warmup_embedding_probe())

    sse_holder: dict[str, Any] = {}
    tasks = [asyncio.create_task(server.wait_closed())]
    if sse_enabled:
        tasks.append(asyncio.create_task(start_sse_server(sse_host, sse_port, server_holder=sse_holder)))

    def _trigger_graceful_shutdown():
        u_server = sse_holder.get("server")
        if u_server is not None:
            u_server.should_exit = True

    try:
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        _trigger_graceful_shutdown()
        for t in pending:
            t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    except (asyncio.CancelledError, KeyboardInterrupt):
        _trigger_graceful_shutdown()
        for t in tasks:
            if not t.done():
                t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(run_server())
