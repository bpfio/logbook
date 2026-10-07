"""敏感信息与凭据自动脱敏引擎

铁律依据 (RULE 8):
- 严禁在公开手记或对外导出的文档中泄露真实生产 IP / 私有域名 / 凭据 Token。
- 统一替换为 RFC 5737 保留文档 IP (192.0.2.x, 198.51.100.x, 203.0.113.x) 或占位符。
"""

import re
from typing import NamedTuple

# 正则匹配私有/敏感 IPv4 (10.x.x.x, 172.16-31.x.x, 192.168.x.x, 运营商 CGNAT 100.64-127.x.x)
RE_PRIVATE_IPV4 = re.compile(
    r"\b(?:10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"172\.(?:1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"100\.(?:6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3})\b"
)

# 正则匹配常见 API Token / Secret / 密码签名
RE_CREDENTIALS = [
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{36,255}\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,255}\b"), "[REDACTED_SLACK_TOKEN]"),
    (re.compile(r"(?i)\b(?:api[_-]?key|secret|password|token)\s*[:=]\s*['\"]?([A-Za-z0-9_\-\.]{8,128})['\"]?"), "[REDACTED_SECRET]"),
    (re.compile(r"-----BEGIN [A-Z ]+ PRIVATE KEY-----[^-]+-----END [A-Z ]+ PRIVATE KEY-----", re.DOTALL), "[REDACTED_PRIVATE_KEY]"),
]


# 正则匹配私有/链路本地 IPv6 (ULA fd00::/8, Link-local fe80::/10)
RE_PRIVATE_IPV6 = re.compile(r"\b(?:fd[0-9a-fA-F]{2}|fe80):[0-9a-fA-F:]+\b")


class SanitizeResult(NamedTuple):
    clean_text: str
    redacted_count: int


def sanitize_text(text: str) -> SanitizeResult:
    """对输入文本执行纯函数脱敏处理。"""
    if not text:
        return SanitizeResult(clean_text=text, redacted_count=0)

    count = 0
    clean = text

    # 1. 凭据清洗
    for pattern, placeholder in RE_CREDENTIALS:
        matches = pattern.findall(clean)
        if matches:
            count += len(matches)
            clean = pattern.sub(placeholder, clean)

    # 2. 私有 IPv4 清洗 (统一转换为 RFC 5737 规范占位符)
    ip_matches = RE_PRIVATE_IPV4.findall(clean)
    if ip_matches:
        count += len(ip_matches)
        clean = RE_PRIVATE_IPV4.sub("192.0.2.x", clean)

    # 3. 私有 IPv6 清洗 (统一转换为 RFC 3849 文档保留段 2001:db8::x)
    ip6_matches = RE_PRIVATE_IPV6.findall(clean)
    if ip6_matches:
        count += len(ip6_matches)
        clean = RE_PRIVATE_IPV6.sub("2001:db8::x", clean)

    return SanitizeResult(clean_text=clean, redacted_count=count)
