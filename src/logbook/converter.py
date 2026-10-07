"""双向 Git Markdown 转换管道

功能:
- 逆向工程解析并无损导入 bpfio/brix/docs/DEVLOG.md 文本中的任务、发现与待办
- 从数据库实时导出 100% 格式对齐的 docs/DEVLOG.md，便于 Git 提交与人类直读
"""

import re
from typing import NamedTuple
from .models import (
    Task, TaskStatus, TaskPriority, TaskType,
    Finding, FindingStatus, FindingSeverity,
    Waiting, WaitingStatus, WaitingCategory
)
from .db import db


class ParseSummary(NamedTuple):
    tasks_imported: int
    findings_imported: int
    waitings_imported: int


async def import_devlog_markdown(project: str, markdown_text: str) -> ParseSummary:
    """解析 Markdown 表格并灌库。"""
    t_count = 0
    f_count = 0
    w_count = 0

    lines = markdown_text.splitlines()
    current_table = None

    for line in lines:
        s = line.strip()
        if not s:
            continue

        if "## 一、" in s and "任务台账" in s:
            current_table = "tasks"
            continue
        elif "## 二、" in s and "发现台账" in s:
            current_table = "findings"
            continue
        elif "## 三、" in s and ("待办" in s or "待用户" in s):
            current_table = "waitings"
            continue
        elif s.startswith("## ") and not s.startswith("## 一、") and not s.startswith("## 二、") and not s.startswith("## 三、"):
            current_table = None
            continue

        if not current_table or not s.startswith("|") or "---|---" in s:
            continue

        cols = [c.strip() for c in s.strip("|").split("|")]

        if current_table == "tasks":
            # | ID | 状态 | 类型 | 标题 | commit | 备注 |
            if len(cols) >= 4 and cols[0] != "ID":
                tid = cols[0]
                raw_status = cols[1].lower()
                status = TaskStatus.PLANNED
                if "closed" in raw_status or "✅" in raw_status:
                    status = TaskStatus.CLOSED
                elif "running" in raw_status or "进行中" in raw_status:
                    status = TaskStatus.RUNNING
                elif "blocked" in raw_status or "⏸" in raw_status:
                    status = TaskStatus.BLOCKED
                elif "wontfix" in raw_status:
                    status = TaskStatus.WONTFIX
                elif "merged" in raw_status:
                    status = TaskStatus.CLOSED

                ttype = TaskType.FIX
                if len(cols) >= 3:
                    raw_type = cols[2].lower()
                    for enum_t in TaskType:
                        if enum_t.value in raw_type:
                            ttype = enum_t
                            break

                title = cols[3] if len(cols) >= 4 else "未命名任务"
                commit = cols[4] if len(cols) >= 5 and cols[4] else None
                proof = None
                notes = cols[5] if len(cols) >= 6 else None
                if notes and "报告" in notes:
                    proof = notes

                # 闭环状态兜底凭据
                if status == TaskStatus.CLOSED and not commit and not proof:
                    commit = "imported_legacy"

                try:
                    task = Task(
                        id=tid,
                        title=title,
                        task_type=ttype,
                        priority=TaskPriority.P2,
                        status=status,
                        commit_hash=commit,
                        proof_link=proof,
                        notes=notes
                    )
                    await db.upsert_task(project, task)
                    t_count += 1
                except Exception:
                    pass

        elif current_table == "findings":
            # | ID | 来源 | 级别 | 状态 | 处置 | 备注 |
            if len(cols) >= 4 and cols[0] != "ID":
                fid = cols[0]
                source = cols[1] if len(cols) >= 2 else "audit"
                prio = FindingSeverity.P2
                if len(cols) >= 3 and cols[2] in ("P1", "P2", "P3"):
                    prio = FindingSeverity(cols[2])

                raw_status = cols[3].lower() if len(cols) >= 4 else "open"
                f_status = FindingStatus.OPEN
                if "fixed" in raw_status or "✅" in raw_status:
                    f_status = FindingStatus.FIXED
                elif "blocked" in raw_status or "⏸" in raw_status:
                    f_status = FindingStatus.BLOCKED
                elif "wontfix" in raw_status:
                    f_status = FindingStatus.WONTFIX
                elif "infix" in raw_status:
                    f_status = FindingStatus.INFIX

                resolution = cols[4] if len(cols) >= 5 else None
                notes = cols[5] if len(cols) >= 6 else None
                summary = notes if notes else f"{fid} 发现项"

                try:
                    finding = Finding(
                        id=fid,
                        source=source,
                        severity=prio,
                        status=f_status,
                        summary=summary,
                        resolution=resolution
                    )
                    await db.upsert_finding(project, finding)
                    f_count += 1
                except Exception:
                    pass

        elif current_table == "waitings":
            # | ID | 类别 | 状态 | 事项 |
            if len(cols) >= 4 and cols[0] != "ID":
                wid = cols[0]
                cat = WaitingCategory.USER if "user" in cols[1].lower() else WaitingCategory.CLOSING
                w_status = WaitingStatus.CLOSED if "closed" in cols[2].lower() else WaitingStatus.OPEN
                desc = cols[3]

                try:
                    waiting = Waiting(
                        id=wid,
                        category=cat,
                        status=w_status,
                        description=desc
                    )
                    await db.upsert_waiting(project, waiting)
                    w_count += 1
                except Exception:
                    pass

    return ParseSummary(tasks_imported=t_count, findings_imported=f_count, waitings_imported=w_count)


async def export_devlog_markdown(project: str) -> str:
    """从数据库生成格式兼容的标准 DEVLOG.md。"""
    tasks = await db.query_tasks(project, limit=200)
    findings = await db.query_findings(project, limit=200)
    waitings = await db.query_waitings(project)
    batches = await db.query_batches(project, limit=10)

    lines = [
        f"# {project.upper()} 开发日志 (DEVLOG) — 项目唯一开发日志与任务台账",
        "",
        "> **规则 (AGENTS.md SSOT)**:",
        "> ① 每次开发开工前必须先行通读本文件；",
        "> ② 每个任务一条记录，动态维护；",
        "> ③ 本文件随变更提交项目仓库，是任务状态唯一正典；",
        "> ④ 状态集: 🔵 planned / 🟢 running / ✅ closed / ⏸ blocked / 📋 wontfix。",
        "> ⑤ 排序: 最新在前。",
        "",
        "---",
        "",
        f"## 一、任务台账 ({len(tasks)})",
        "",
        "| ID | 状态 | 类型 | 标题 | commit | 备注 |",
        "|---|---|---|---|---|---|",
    ]

    for t in tasks:
        st_icon = "✅ closed" if t.status == TaskStatus.CLOSED else t.status.value
        commit_str = t.commit_hash or "-"
        proof_or_notes = t.proof_link or t.notes or "-"
        lines.append(f"| {t.id} | {st_icon} | {t.task_type.value} | {t.title} | {commit_str} | {proof_or_notes} |")

    lines.extend([
        "",
        "---",
        "",
        f"## 二、发现台账 ({len(findings)})",
        "",
        "| ID | 来源 | 级别 | 状态 | 处置 | 备注 |",
        "|---|---|---|---|---|---|",
    ])

    for f in findings:
        res = f.resolution or "-"
        lines.append(f"| {f.id} | {f.source} | {f.severity.value} | {f.status.value} | {res} | {f.summary} |")

    lines.extend([
        "",
        "---",
        "",
        f"## 三、待办/待用户 ({len(waitings)})",
        "",
        "| ID | 类别 | 状态 | 事项 |",
        "|---|---|---|---|",
    ])

    for w in waitings:
        lines.append(f"| {w.id} | {w.category.value} | {w.status.value} | {w.description} |")

    if batches:
        lines.extend([
            "",
            "---",
            "",
            "## 四、批次演进记录",
            "",
        ])
        for b in batches:
            lines.append(f"### [{b.id}] {b.title} — {b.status.value}")
            if b.summary:
                lines.append(f"- **任务源**: {b.summary}")
            if b.methodology_notes:
                lines.append(f"- **排障方法论与会话经验**: {b.methodology_notes}")
            lines.append("")

    return "\n".join(lines) + "\n"
