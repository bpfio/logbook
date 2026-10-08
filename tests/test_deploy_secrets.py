"""deploy/qnap 密钥迁出 (P2-1) 冒烟测试

验证内容:
1. compose.yaml 不含明文凭据 (星火 API Key / 数据库口令)
2. compose.yaml 的 ${VAR} 引用均能在 .env / .env.example 中找到定义
3. .env.example 只含占位符, 不含真实值
4. .gitignore 已忽略 deploy/qnap/.env

说明: compose 的 .env 加载机制是 docker compose 运行时行为, 本测试模拟其
变量解析逻辑 (同目录 .env 的 KEY=VALUE 覆盖), 完整加载验证见交卷文档的
手工验证步骤 (docker compose config)。
"""

import re
from pathlib import Path

QNAP_DIR = Path(__file__).resolve().parent.parent / "deploy" / "qnap"


def _parse_env_file(path: Path) -> dict:
    """解析简单 KEY=VALUE 格式 (compose .env 子集, 忽略注释与空行)。"""
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        key, _, value = line.partition("=")
        result[key.strip()] = value.strip()
    return result


def _compose_var_refs(text: str) -> set:
    """提取 compose 文本中的 ${VAR} / ${VAR:-default} 引用。"""
    return set(re.findall(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-[^}]*)?\}", text))


def test_compose_has_no_plaintext_secrets():
    text = (QNAP_DIR / "compose.yaml").read_text(encoding="utf-8")
    # 星火 key 片段与历史明文口令不得再出现
    assert "3ea8786db03a" not in text
    assert "logbook_qnap_password_2026" not in text
    assert "EMBEDDING_API_KEY=${EMBEDDING_API_KEY}" in text
    assert "POSTGRES_PASSWORD=${LOGBOOK_DB_PASSWORD}" in text


def test_compose_var_refs_resolvable():
    compose_text = (QNAP_DIR / "compose.yaml").read_text(encoding="utf-8")
    env = _parse_env_file(QNAP_DIR / ".env")
    env_example = _parse_env_file(QNAP_DIR / ".env.example")
    defined = set(env) | set(env_example)
    missing = _compose_var_refs(compose_text) - defined
    assert not missing, f"compose 引用了未定义变量: {missing}"


def test_env_example_is_placeholder_only():
    text = (QNAP_DIR / ".env.example").read_text(encoding="utf-8")
    assert "3ea8786db03a" not in text
    assert "logbook_qnap_password_2026" not in text


def test_gitignore_covers_qnap_env():
    gi = Path(__file__).resolve().parent.parent / ".gitignore"
    patterns = [
        ln.strip() for ln in gi.read_text(encoding="utf-8").splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert "deploy/qnap/.env" in patterns
