"""Logbook 核心领域模型与强类型契约 (Pydantic v2)

设计原则:
- 状态机不变量硬断言 (闭环必须有 commit / proof_link 证据)
- 全生命周期时间度量 (started_at, closed_at, duration_seconds)
- 密级防线与故障四要素强类型
"""

from datetime import datetime
from enum import Enum
from typing import Any
from pydantic import BaseModel, Field, model_validator
from .time_sync import get_beijing_now


class TaskStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    BLOCKED = "blocked"
    CLOSED = "closed"
    WONTFIX = "wontfix"


class TaskPriority(str, Enum):
    P0 = "P0"  # 紧急故障 / 阻塞主线
    P1 = "P1"  # 严重缺陷 / 核心功能
    P2 = "P2"  # 中度问题 / 体验演练
    P3 = "P3"  # 轻微优化 / 文档注记


class TaskType(str, Enum):
    FEAT = "feat"
    FIX = "fix"
    VERIFY = "verify"
    INVESTIGATION = "investigation"
    DRILL = "drill"
    DOCS = "docs"
    OPS = "ops"
    DEPLOY = "deploy"


class FindingSeverity(str, Enum):
    P1 = "P1"
    P2 = "P2"
    P3 = "P3"


class FindingStatus(str, Enum):
    OPEN = "open"
    INFIX = "infix"
    FIXED = "fixed"
    WONTFIX = "wontfix"
    BLOCKED = "blocked"


class WaitingCategory(str, Enum):
    USER = "user"          # 需人类用户决策/物理授权
    CLOSING = "closing"    # 批次收尾动作
    EXTERNAL = "external"  # 外部环境依赖


class WaitingStatus(str, Enum):
    OPEN = "open"
    CLOSED = "closed"


class BatchStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    HALTED = "halted"      # 停机保存态


class DevLogVisibility(str, Enum):
    PROJECT_PRIVATE = "project_private"  # 仅本项目 Agent 检索
    ORG_INTERNAL = "org_internal"        # 内部项目可检索
    PUBLIC_SAFE = "public_safe"          # 开源安全 (无任何私有细节)


# ============================================================================
# 实体模型
# ============================================================================

class Task(BaseModel):
    id: str = Field(..., max_length=32, description="项目内短编号，如 F19, W10")
    title: str = Field(..., max_length=256)
    task_type: TaskType = Field(default=TaskType.FIX)
    priority: TaskPriority = Field(default=TaskPriority.P2)
    status: TaskStatus = Field(default=TaskStatus.PLANNED)
    assignee: str | None = Field(default="agy", max_length=64, description="责任主体/Agent，如 agy, yupeng")
    parent_id: str | None = Field(default=None, max_length=32, description="父任务短编号，支持两级树形解耦")
    commit_hash: str | None = Field(default=None, max_length=128)
    proof_link: str | None = Field(default=None, max_length=512)
    notes: str | None = None
    tags: list[str] = Field(default_factory=list, description="业务与技术领域打标")
    batch_id: str | None = Field(default=None, max_length=32)

    created_at: datetime = Field(default_factory=get_beijing_now)
    updated_at: datetime = Field(default_factory=get_beijing_now)
    started_at: datetime | None = None
    closed_at: datetime | None = None
    duration_seconds: int | None = None

    @model_validator(mode="after")
    def validate_invariants(self) -> "Task":
        # 1. 闭环铁律断言：closed 状态必须提供真实提交或验证凭证
        if self.status == TaskStatus.CLOSED:
            has_evidence = bool(
                self.commit_hash or self.proof_link or
                (self.notes and self.task_type in (TaskType.DRILL, TaskType.INVESTIGATION, TaskType.OPS, TaskType.DOCS))
            )
            if not has_evidence:
                raise ValueError("闭环铁律：标记为 closed 的任务必须提供 commit_hash、proof_link 或非代码演练实测 notes 证据锚点！")
            if not self.closed_at:
                self.closed_at = get_beijing_now()
            if self.started_at and not self.duration_seconds:
                self.duration_seconds = max(0, int((self.closed_at - self.started_at).total_seconds()))

        # 2. 开工时刻自适应打点
        if self.status == TaskStatus.RUNNING and not self.started_at:
            self.started_at = get_beijing_now()

        return self


class Finding(BaseModel):
    id: str = Field(..., max_length=64, description="缺陷编号，如 F-322-5, AUDIT-P1-1")
    source: str = Field(..., max_length=64, description="来源，如 audit, drill, realmachine")
    severity: FindingSeverity = Field(default=FindingSeverity.P2)
    status: FindingStatus = Field(default=FindingStatus.OPEN)
    task_id: str | None = Field(default=None, max_length=32, description="处置责任任务 ID")
    reporter: str = Field(default="audit", max_length=64, description="发现人或工具来源")
    summary: str = Field(..., description="缺陷现象与机理描述")
    resolution: str | None = Field(default=None, description="处置说明与备案结论")
    discovered_at: datetime = Field(default_factory=get_beijing_now)
    resolved_at: datetime | None = None


class Waiting(BaseModel):
    id: str = Field(..., max_length=32, description="待办编号，如 WAIT-F320, CLOSE-1")
    category: WaitingCategory = Field(default=WaitingCategory.USER)
    status: WaitingStatus = Field(default=WaitingStatus.OPEN)
    owner: str = Field(default="user", max_length=64, description="等待裁决责任人或外部主体")
    description: str = Field(..., description="事项描述")
    resolution: str | None = Field(default=None, description="裁决与办结记录")
    blocked_at: datetime = Field(default_factory=get_beijing_now)
    resolved_at: datetime | None = None


class Batch(BaseModel):
    id: str = Field(..., max_length=32, description="批次编号，如 DEV-2026-10-07-02")
    title: str = Field(..., max_length=256)
    status: BatchStatus = Field(default=BatchStatus.RUNNING)
    branch_name: str | None = Field(default=None, max_length=128)
    summary: str | None = None
    methodology_notes: str | None = Field(default=None, description="排障方法论三则与会话收口经验")
    created_at: datetime = Field(default_factory=get_beijing_now)
    closed_at: datetime | None = None


class DevLog(BaseModel):
    id: int | None = None
    project_id: str = Field(..., max_length=32)
    task_id: str | None = Field(default=None, max_length=32)
    title: str = Field(..., max_length=256)
    author: str = Field(default="agy", max_length=64, description="记录者 Agent 或人类专家")
    # 根因四要素 (必填)
    problem: str = Field(..., description="故障现象与具体复现路径")
    root_cause: str = Field(..., description="机理定位与内核/代码行穿透分析")
    solution: str = Field(..., description="明确修复逻辑与架构变更")
    evidence: str = Field(..., description="复现与修复后的实测对比证据")
    visibility: DevLogVisibility = Field(default=DevLogVisibility.PROJECT_PRIVATE)
    tags: list[str] = Field(default_factory=list)
    embedding: list[float] | None = None
    occurred_at: datetime = Field(default_factory=get_beijing_now)
    created_at: datetime = Field(default_factory=get_beijing_now)
    updated_at: datetime = Field(default_factory=get_beijing_now)


class Rule(BaseModel):
    id: str = Field(..., max_length=64)
    category: str = Field(default="general", max_length=32, description="领域分类: network, kernel, database, security 等")
    title: str = Field(..., max_length=256)
    summary: str = Field(..., description="一句话核心原则")
    bad_practice: str = Field(..., description="错误示范 (Anti-Pattern)")
    good_practice: str = Field(..., description="正确做法 (Best Practice)")
    constraints: str | None = Field(default=None, description="边界约束与红线说明")
    created_at: datetime = Field(default_factory=get_beijing_now)
    updated_at: datetime = Field(default_factory=get_beijing_now)
