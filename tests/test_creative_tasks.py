"""Unit tests for creative and narrative engineering task types."""

import pytest
from logbook.models import Task, TaskStatus, TaskPriority, TaskType
from logbook.normalizer import normalize_task_type


def test_creative_task_types_exist():
    assert TaskType.OUTLINE == "outline"
    assert TaskType.DRAFT == "draft"
    assert TaskType.POLISH == "polish"
    assert TaskType.CONTINUITY == "continuity"
    assert TaskType.LORE == "lore"


def test_normalize_creative_task_types():
    assert normalize_task_type("outline") == "outline"
    assert normalize_task_type("plot") == "outline"
    assert normalize_task_type("beats") == "outline"
    assert normalize_task_type("draft") == "draft"
    assert normalize_task_type("write") == "draft"
    assert normalize_task_type("chapter") == "draft"
    assert normalize_task_type("polish") == "polish"
    assert normalize_task_type("refine") == "polish"
    assert normalize_task_type("revise") == "polish"
    assert normalize_task_type("continuity") == "continuity"
    assert normalize_task_type("audit") == "continuity"
    assert normalize_task_type("check") == "continuity"
    assert normalize_task_type("lore") == "lore"
    assert normalize_task_type("setting") == "lore"
    assert normalize_task_type("worldbuilding") == "lore"


def test_creative_task_closed_with_notes():
    # 创作类任务（大纲、草稿、润色、设定等）允许以详实 notes 闭环
    t = Task(
        id="CH-001",
        title="第一章：雪夜离宗",
        task_type=TaskType.DRAFT,
        status=TaskStatus.CLOSED,
        notes="已完成 3200 字起草，场景冲突与章尾留钩自洽",
    )
    assert t.status == TaskStatus.CLOSED
    assert t.closed_at is not None


def test_creative_task_closed_with_commit():
    # 创作类任务如果走 Git 提交 Markdown，以 commit_hash 闭环同样合规
    t = Task(
        id="CH-002",
        title="第二章：寒潭残鼎",
        task_type=TaskType.DRAFT,
        status=TaskStatus.CLOSED,
        commit_hash="git_commit_789abc",
    )
    assert t.status == TaskStatus.CLOSED
    assert t.commit_hash == "git_commit_789abc"


def test_creative_task_closed_without_evidence_rejected():
    # 创作类任务若没有任何证据（无 commit、无 link、无 notes），严格拒绝闭环
    with pytest.raises(ValueError, match="闭环铁律"):
        Task(
            id="CH-003",
            title="第三章：宗门追兵",
            task_type=TaskType.DRAFT,
            status=TaskStatus.CLOSED,
        )


def test_engineering_code_task_invariant_preserved():
    # 存量软件工程代码任务（feat/fix）仍然禁止仅凭 notes 闭环，保持铁律不变量
    with pytest.raises(ValueError, match="闭环铁律"):
        Task(
            id="FEAT-01",
            title="新增核心功能",
            task_type=TaskType.FEAT,
            status=TaskStatus.CLOSED,
            notes="代码已写完，但未提供 commit 或 proof_link",
        )
