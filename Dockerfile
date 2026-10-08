# Multi-stage ultra-fast lightweight Dockerfile for Logbook
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app/src \
    TZ=Asia/Shanghai

WORKDIR /app

# Install uv for ultra-fast package installation
COPY --from=ghcr.io/astral-sh/uv:latest /uv /bin/uv

# Copy project metadata
COPY pyproject.toml README.md ./

# Install dependencies into system Python
# (与 pyproject.toml dependencies 保持一致, 新增依赖两处同步)
RUN uv pip install --system \
    "pydantic>=2.10.0" \
    "asyncpg>=0.30.0" \
    "mcp>=1.0.0" \
    "rich>=13.8.0" \
    "httpx>=0.28.0" \
    "asyncssh>=2.20.0" \
    "click>=8.1.0"

# Copy application source
COPY src/ /app/src/

# Expose SSH (22) for Agent MCP & CLI access
EXPOSE 22

# Volume for persistent host keys and local data
VOLUME ["/app/data"]

CMD ["python", "-m", "logbook.ssh_server"]
