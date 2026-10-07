"""原生轻量 SNTP 探针与北京时间编排模块

特性:
- 纯 Python socket 实现 (零外部重型依赖)
- 支持国内主流授时中心 (阿里云、腾讯云、NTP 授时池)
- 时钟漂移核验 (Drift Detection)
- 全链路统一输出 Asia/Shanghai (UTC+8) 标准时间
"""

import socket
import struct
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from typing import NamedTuple

BEIJING_TZ = ZoneInfo("Asia/Shanghai")
NTP_SERVERS = ["ntp.aliyun.com", "ntp.tencent.com", "cn.pool.ntp.org"]
NTP_EPOCH_OFFSET = 2208988800  # 1900-01-01 到 1970-01-01 的秒数


class DriftReport(NamedTuple):
    server: str
    drift_seconds: float
    is_synchronized: bool
    message: str


def get_beijing_now() -> datetime:
    """获取当前标准的北京时间 (带时区信息)。"""
    return datetime.now(BEIJING_TZ)


def format_beijing(dt: datetime | None, with_timezone: bool = False) -> str:
    """将时间格式化为人类可读的北京时间字符串。"""
    if dt is None:
        return "-"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    b_dt = dt.astimezone(BEIJING_TZ)
    if with_timezone:
        return b_dt.strftime("%Y-%m-%d %H:%M:%S+08:00")
    return b_dt.strftime("%Y-%m-%d %H:%M:%S")


def query_sntp_offset(server: str = "ntp.aliyun.com", timeout: float = 1.5) -> float:
    """通过 RFC 5905 SNTP 查询本地时钟与标准时间的漂移 (秒)。

    返回值:
        offset (秒): 正数表示本地时钟慢于标准时间，负数表示快于标准时间。
    """
    client = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    client.settimeout(timeout)
    try:
        # NTP v4 客户端请求报文 (48 字节, LI=0, VN=4, Mode=3)
        packet = b"\x23" + 47 * b"\0"
        t1 = time.time()
        client.sendto(packet, (server, 123))
        data, _ = client.recvfrom(1024)
        t4 = time.time()

        if len(data) < 48:
            raise ValueError(f"SNTP 响应报文长度不足: {len(data)} 字节")

        # 提取服务端 Transmit Timestamp (偏移量 40..48 字节)
        sec, frac = struct.unpack("!2I", data[40:48])
        ntp_time = (sec - NTP_EPOCH_OFFSET) + (frac / (2**32))

        # 计算往返网络延迟与时钟偏移 offset = ((t2 - t1) + (t3 - t4)) / 2
        # 在单包 SNTP 简化模型中，网络往返对称假设:
        offset = ntp_time - ((t1 + t4) / 2)
        return offset
    finally:
        client.close()


def check_clock_drift(max_allowed_drift: float = 1.0) -> DriftReport:
    """遍历授时池检查本地时钟漂移情况。"""
    errors = []
    for srv in NTP_SERVERS:
        try:
            drift = query_sntp_offset(srv, timeout=1.2)
            abs_drift = abs(drift)
            is_sync = abs_drift <= max_allowed_drift
            msg = (
                f"时钟同步正常 (漂移 {drift:+.3f}s <= 阈值 {max_allowed_drift}s)"
                if is_sync
                else f"时钟漂移警告! 本地时钟偏移 {drift:+.3f}s (超过阈值 {max_allowed_drift}s)"
            )
            return DriftReport(server=srv, drift_seconds=drift, is_synchronized=is_sync, message=msg)
        except Exception as e:
            errors.append(f"{srv}: {e}")

    # 所有公网授时源探测均失败 (离线或内网环境)
    return DriftReport(
        server="none",
        drift_seconds=0.0,
        is_synchronized=True,
        message=f"授时网络不可达，跳过公网 NTP 探测 ({'; '.join(errors)})",
    )
