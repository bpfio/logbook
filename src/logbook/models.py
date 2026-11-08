"""Logbook 核心领域模型与强类型契约 (Pydantic v2) - 0.8.0 极简正典

三大核心领域模型体系：
1. 权威项目注册与 Git 仓库映射 (Project)
2. 多 Agent 对讲信箱与分布式文件租约 (AgentMessage, FileLease)
3. 通用数据集与结构化事实记录 (Dataset, DataRecord)
"""

import re
from datetime import datetime, date
from decimal import Decimal
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, field_validator
from .time_sync import get_beijing_now


def slug_to_schema_name(slug: str) -> str:
    """将 Git 仓库标识 (如 bpfio/logbook, io/TS) 安全映射为 PostgreSQL 物理 Schema 名。"""
    if not slug or not slug.strip():
        raise ValueError("项目坐标禁止为空！")
    if not any(c.isalnum() for c in slug):
        raise ValueError(f"项目坐标 '{slug}' 格式非法，必须包含有效英文字母或数字！")
    parts = slug.strip().split("/")
    valid_parts = [p for p in parts if p]
    if not valid_parts:
        raise ValueError(f"项目坐标 '{slug}' 格式非法！")
    repo_name = valid_parts[-1].lower()
    sanitized = re.sub(r"[^a-z0-9_]", "_", repo_name)
    sanitized = re.sub(r"_+", "_", sanitized).strip("_")
    if not sanitized:
        raise ValueError(f"项目坐标 '{slug}' 无法转换为有效 Schema 名称！")
    if not sanitized[0].isalpha():
        sanitized = f"p_{sanitized}"
    return sanitized[:32]


class Project(BaseModel):
    """权威项目注册实体 (SSOT: Git 仓库坐标与物理 Schema 映射)。"""
    slug: str = Field(..., max_length=64, description="权威 Git 坐标，如 bpfio/logbook, io/TS, brix")
    schema_name: str = Field(..., max_length=32, description="物理 PostgreSQL Schema 名称")
    title: str = Field(..., max_length=256, description="项目全称或中文名称")
    description: str | None = Field(default=None, description="项目描述与定位")
    created_at: datetime = Field(default_factory=get_beijing_now)
    updated_at: datetime = Field(default_factory=get_beijing_now)

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        s = v.strip()
        if not re.match(r"^[a-zA-Z0-9_.-]+(/[a-zA-Z0-9_.-]+)?$", s):
            raise ValueError(f"项目标识符格式非法: '{v}'，必须符合 'owner/repo' 或 'repo' 规范！")
        return s

    @field_validator("schema_name")
    @classmethod
    def validate_schema_name(cls, v: str) -> str:
        s = v.strip().lower()
        if not re.match(r"^[a-z0-9_]{2,32}$", s):
            raise ValueError(f"物理 Schema 名称非法: '{v}'，必须由 2~32 位小写字母、数字或下划线组成！")
        return s


# ============================================================================
# 多 Agent 对讲信箱与文件租约模型 (0.3.0)
# ============================================================================

class AgentMessageSend(BaseModel):
    project_id: str = Field(..., max_length=32, description="归属项目代号")
    from_agent: str = Field(..., max_length=64, description="发件人 Agent")
    from_ip: str = Field(default="0.0.0.0", max_length=45, description="发件端节点 IP 地址")
    to_agent: str = Field(..., max_length=64, description="收件人 Agent")
    to_ip: str = Field(default="0.0.0.0", max_length=45, description="收件端节点 IP 地址")
    subject: str = Field(..., max_length=256, description="消息主题")
    content: str = Field(..., description="消息正文 (Markdown)")
    task_id: str | None = Field(default=None, max_length=32, description="关联任务短编号")
    thread_id: str | None = Field(default=None, max_length=64, description="会话线索")


class AgentMessage(AgentMessageSend):
    id: int
    is_read: bool = False
    read_at: datetime | None = None
    read_by_agent: str | None = Field(default=None, max_length=64, description="实际读取并核销信件的收件人")
    is_archived: bool = Field(default=False, description="收件人归档标记 (仅影响本人信箱视图)")
    archived_at: datetime | None = None
    created_at: datetime = Field(default_factory=get_beijing_now)


class FileLeaseAcquire(BaseModel):
    project_id: str = Field(..., max_length=32, description="归属项目代号")
    agent_name: str = Field(..., max_length=64, description="申请租约的 Agent")
    agent_ip: str = Field(default="0.0.0.0", max_length=45, description="申请租约的节点 IP 地址")
    file_path: str = Field(..., max_length=512, description="文件相对路径")
    duration_seconds: int = Field(default=300, ge=10, le=3600, description="租约有效时长 (秒，默认 5 分钟)")


class FileLease(BaseModel):
    id: int
    project_id: str
    agent_name: str
    agent_ip: str = Field(default="0.0.0.0", max_length=45, description="持锁 Agent 节点 IP")
    file_path: str
    lease_expires_at: datetime
    created_at: datetime = Field(default_factory=get_beijing_now)


# ============================================================================
# 通用数据集与结构化事实记录模型 (0.5.0)
# ============================================================================

class DatasetCategory(str, Enum):
    FINANCE = "finance"
    OSS_METRICS = "oss_metrics"
    TRADE = "trade"
    TAX = "tax"
    RESEARCH = "research"
    GENERAL = "general"


class Dataset(BaseModel):
    """数据集字典定义模型 (Schema & Metadata)."""
    code: str = Field(..., max_length=64, description="数据集唯一标识码，如 safe_forex, oss_benchmark")
    name: str = Field(..., max_length=256, description="数据集全称或中文名称")
    category: str = Field(default="general", max_length=32, description="业务领域分类")
    field_schema: list[dict[str, Any]] = Field(default_factory=list, description="字段元数据规范列表")
    alias_map: dict[str, str] = Field(default_factory=dict, description="历史版本与同义词映射表")
    json_schema: dict[str, Any] | None = Field(default=None, description="声明式 JSON Schema 校验契约")
    description: str | None = Field(default=None, description="数据集口径与业务填报规范说明")
    created_at: datetime = Field(default_factory=get_beijing_now)
    updated_at: datetime = Field(default_factory=get_beijing_now)

    @field_validator("code")
    @classmethod
    def validate_code(cls, v: str) -> str:
        s = v.strip().lower()
        if not re.match(r"^[a-z0-9_.-]{2,64}$", s):
            raise ValueError(f"数据集编码非法: '{v}'，必须由 2~64 位小写字母、数字、点或下划线组成！")
        return s


class RecordStatus(str, Enum):
    RECORDED = "recorded"
    RECONCILED = "reconciled"
    VERIFIED = "verified"
    CANCELLED = "cancelled"
    ACTIVE = "active"
    PLANNED = "planned"
    RUNNING = "running"
    REVIEW = "review"
    BLOCKED = "blocked"
    CLOSED = "closed"
    WONTFIX = "wontfix"
    OPEN = "open"
    INFIX = "infix"
    FIXED = "fixed"
    COMPLETED = "completed"
    HALTED = "halted"
    ARCHIVED = "archived"
    READ = "read"
    UNREAD = "unread"
    DELIVERED = "delivered"
    PROJECT_PRIVATE = "project_private"
    ORG_INTERNAL = "org_internal"
    PUBLIC_SAFE = "public_safe"


class DataRecord(BaseModel):
    """通用事实数据行模型 (Strong Spine + Flexible Ribs)."""
    id: int | None = None
    project_id: str = Field(..., max_length=64, description="归属项目代号")
    dataset_code: str = Field(..., max_length=64, description="关联数据集唯一标识码")
    parent_id: int | None = Field(default=None, description="父单据记录 ID (支持主子表分录)")
    batch_id: str | None = Field(default=None, max_length=64, description="导入批次号")

    # 强脊梁 (Strong Spine)
    doc_no: str = Field(..., max_length=128, description="核心业务单号/关单号/发票号/组件版本")
    entity_name: str | None = Field(default=None, max_length=256, description="往来单位/境外付款人/组件仓库名")
    amount: Decimal = Field(default=Decimal("0.0000"), description="核心金额/指标数值 (高精度, 无浮点误差)")
    currency: str = Field(default="CNY", max_length=16, description="币种或量纲单位，如 USD, CNY, QPS, MS")
    event_date: date | str = Field(default_factory=lambda: date.today().isoformat(), description="业务发生日期或测试日期 (YYYY-MM-DD)")
    status: str = Field(default="recorded", max_length=32, description="单据状态")

    # 软肋骨 (Flexible Ribs)
    payload: dict[str, Any] = Field(default_factory=dict, description="动态属性载荷 (JSONB)")

    # 语义检索向量 (768 维，讯飞星火 MaaS / 本地降级向量)
    embedding: list[float] | None = Field(default=None, description="768 维语义向量")

    created_at: datetime = Field(default_factory=get_beijing_now)
    updated_at: datetime = Field(default_factory=get_beijing_now)

    @field_validator("event_date", mode="before")
    @classmethod
    def parse_event_date(cls, v: Any) -> Any:
        if v is None or (isinstance(v, str) and not v.strip()):
            return date.today().isoformat()
        return v

    @field_validator("amount", mode="before")
    @classmethod
    def parse_amount(cls, v: Any) -> Decimal:
        if v is None or (isinstance(v, str) and not v.strip()):
            return Decimal("0.0000")
        try:
            return Decimal(str(v).strip())
        except Exception:
            raise ValueError(f"金额/数值格式非法: '{v}'")
