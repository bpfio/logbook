"""双向 Git Markdown 转换管道 - 0.8.0 极简正典 (直接消费/导入 shared.records)

功能:
- 逆向工程解析并无损导入 DEVLOG.md 文本中的任务、发现与待办到通用事实表 (shared.records)
- 从数据库通用事实表实时导出 100% 格式对齐的 DEVLOG.md，便于 Git 提交与人类直读
"""

import re
from typing import NamedTuple
from decimal import Decimal
from .models import DataRecord
from .db import db


class ParseSummary(NamedTuple):
    tasks_imported: int
    findings_imported: int
    waitings_imported: int
    batches_imported: int = 0


async def import_devlog_markdown(project: str, markdown_text: str) -> ParseSummary:
    """解析 Markdown 表格与批次段落并灌入 shared.records 通用事实表。"""
    t_count = 0
    f_count = 0
    w_count = 0
    b_count = 0

    lines = markdown_text.splitlines()

    # 1. 首先解析所有批次段落 (## [DEV-...] ...)
    batches_to_save: list[tuple[dict, list[str]]] = []
    current_batch_id = None
    current_batch_title = None
    current_batch_status = "completed"
    current_batch_lines: list[str] = []

    batch_header_re = re.compile(r"^##\s+\[(DEV-[^\]]+)\]\s+(.*?)(?:\s+—\s+(.*?))?$")

    for line in lines:
        s = line.strip()
        m = batch_header_re.match(s)
        if m:
            if current_batch_id:
                # 结算上一个批次
                summary_lines = []
                methodology_lines = []
                for bl in current_batch_lines:
                    if any(bl.startswith(k) for k in ("- **任务源**:", "- **✅ 已并入", "- **✅ 已交卷", "- **⏸ 未办结")):
                        summary_lines.append(bl)
                    elif any(bl.startswith(k) for k in ("- **排障方法论", "- **台账", "- **⏭ 待办", "- **📋 备案", "- **方法入规程")):
                        methodology_lines.append(bl)
                    elif bl.startswith("- "):
                        summary_lines.append(bl)
                batches_to_save.append((
                    {
                        "id": current_batch_id,
                        "title": current_batch_title or current_batch_id,
                        "status": current_batch_status,
                        "summary": "\n".join(summary_lines) if summary_lines else None,
                        "methodology_notes": "\n".join(methodology_lines) if methodology_lines else None,
                    },
                    current_batch_lines
                ))
            current_batch_id = m.group(1).strip()
            current_batch_title = m.group(2).strip()
            raw_st = m.group(3).strip() if m.group(3) else ""
            if any(k in raw_st for k in ("停机", "halted", "⏸")):
                current_batch_status = "halted"
            elif any(k in raw_st for k in ("闭环", "closed", "completed", "✅")):
                current_batch_status = "completed"
            elif any(k in raw_st for k in ("进行中", "running", "🟢")):
                current_batch_status = "running"
            else:
                current_batch_status = "planned"
            current_batch_lines = []
        elif current_batch_id:
            if s.startswith("## ") and not s.startswith("## [DEV-"):
                # 退出批次段落
                summary_lines = []
                methodology_lines = []
                for bl in current_batch_lines:
                    if any(bl.startswith(k) for k in ("- **任务源**:", "- **✅ 已并入", "- **✅ 已交卷", "- **⏸ 未办结")):
                        summary_lines.append(bl)
                    elif any(bl.startswith(k) for k in ("- **排障方法论", "- **台账", "- **⏭ 待办", "- **📋 备案", "- **方法入规程")):
                        methodology_lines.append(bl)
                    elif bl.startswith("- "):
                        summary_lines.append(bl)
                batches_to_save.append((
                    {
                        "id": current_batch_id,
                        "title": current_batch_title or current_batch_id,
                        "status": current_batch_status,
                        "summary": "\n".join(summary_lines) if summary_lines else None,
                        "methodology_notes": "\n".join(methodology_lines) if methodology_lines else None,
                    },
                    current_batch_lines
                ))
                current_batch_id = None
                current_batch_lines = []
            elif s:
                current_batch_lines.append(s)

    if current_batch_id:
        summary_lines = []
        methodology_lines = []
        for bl in current_batch_lines:
            if any(bl.startswith(k) for k in ("- **任务源**:", "- **✅ 已并入", "- **✅ 已交卷", "- **⏸ 未办结")):
                summary_lines.append(bl)
            elif any(bl.startswith(k) for k in ("- **排障方法论", "- **台账", "- **⏭ 待办", "- **📋 备案", "- **方法入规程")):
                methodology_lines.append(bl)
            elif bl.startswith("- "):
                summary_lines.append(bl)
        batches_to_save.append((
            {
                "id": current_batch_id,
                "title": current_batch_title or current_batch_id,
                "status": current_batch_status,
                "summary": "\n".join(summary_lines) if summary_lines else None,
                "methodology_notes": "\n".join(methodology_lines) if methodology_lines else None,
            },
            current_batch_lines
        ))

    # 灌入所有批次
    latest_batch_id = None
    batch_records = []
    for batch_dict, _ in batches_to_save:
        if not latest_batch_id:
            latest_batch_id = batch_dict["id"]
        rec = DataRecord(
            project_id=project,
            dataset_code="sys_batch",
            doc_no=batch_dict["id"],
            entity_name=batch_dict["title"],
            status=batch_dict["status"],
            amount=Decimal("0.0000"),
            payload={
                "summary": batch_dict["summary"],
                "methodology_notes": batch_dict["methodology_notes"],
            }
        )
        batch_records.append(rec)
        b_count += 1
    if batch_records:
        await db.bulk_upsert_records(batch_records)

    # 2. 解析任务、发现与待办表格
    current_table = None
    task_records = []
    finding_records = []
    waiting_records = []

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
                status = "planned"
                if "closed" in raw_status or "✅" in raw_status or "merged" in raw_status:
                    status = "closed"
                elif "running" in raw_status or "进行中" in raw_status:
                    status = "running"
                elif "blocked" in raw_status or "⏸" in raw_status:
                    status = "blocked"
                elif "wontfix" in raw_status:
                    status = "wontfix"

                ttype = cols[2].lower() if len(cols) >= 3 else "fix"
                title = cols[3] if len(cols) >= 4 else "未命名任务"
                commit = cols[4] if len(cols) >= 5 and cols[4] else None
                proof = None
                notes = cols[5] if len(cols) >= 6 else None
                if notes and "报告" in notes:
                    proof = notes

                if status == "closed" and not commit and not proof:
                    commit = "imported_legacy"

                try:
                    rec = DataRecord(
                        project_id=project,
                        dataset_code="sys_task",
                        doc_no=tid,
                        entity_name=title,
                        status=status,
                        amount=Decimal("0.0000"),
                        payload={
                            "task_type": ttype,
                            "priority": "P2",
                            "commit_hash": commit,
                            "proof_link": proof,
                            "notes": notes,
                            "batch_id": latest_batch_id,
                        }
                    )
                    task_records.append(rec)
                    t_count += 1
                except Exception:
                    pass

        elif current_table == "findings":
            # | ID | 来源 | 级别 | 状态 | 处置 | 备注 |
            if len(cols) >= 4 and cols[0] != "ID":
                fid = cols[0]
                source = cols[1] if len(cols) >= 2 else "audit"
                prio = cols[2] if len(cols) >= 3 and cols[2] in ("P1", "P2", "P3") else "P2"

                raw_status = cols[3].lower() if len(cols) >= 4 else "open"
                f_status = "open"
                if "fixed" in raw_status or "✅" in raw_status:
                    f_status = "fixed"
                elif "blocked" in raw_status or "⏸" in raw_status:
                    f_status = "blocked"
                elif "wontfix" in raw_status:
                    f_status = "wontfix"
                elif "infix" in raw_status:
                    f_status = "infix"

                resolution = cols[4] if len(cols) >= 5 else None
                notes = cols[5] if len(cols) >= 6 else None
                summary = notes if notes else f"{fid} 发现项"

                try:
                    rec = DataRecord(
                        project_id=project,
                        dataset_code="sys_finding",
                        doc_no=fid,
                        entity_name=summary,
                        status=f_status,
                        amount=Decimal("0.0000"),
                        payload={
                            "source": source,
                            "severity": prio,
                            "resolution": resolution,
                            "summary": summary,
                        }
                    )
                    finding_records.append(rec)
                    f_count += 1
                except Exception:
                    pass

        elif current_table == "waitings":
            # | ID | 类别 | 状态 | 事项 |
            if len(cols) >= 4 and cols[0] != "ID":
                wid = cols[0]
                cat = "user" if "user" in cols[1].lower() else "closing"
                w_status = "closed" if "closed" in cols[2].lower() else "open"
                desc = cols[3]

                try:
                    rec = DataRecord(
                        project_id=project,
                        dataset_code="sys_waiting",
                        doc_no=wid,
                        entity_name=desc,
                        status=w_status,
                        amount=Decimal("0.0000"),
                        payload={
                            "category": cat,
                            "description": desc,
                        }
                    )
                    waiting_records.append(rec)
                    w_count += 1
                except Exception:
                    pass

    if task_records:
        await db.bulk_upsert_records(task_records)
    if finding_records:
        await db.bulk_upsert_records(finding_records)
    if waiting_records:
        await db.bulk_upsert_records(waiting_records)

    return ParseSummary(tasks_imported=t_count, findings_imported=f_count, waitings_imported=w_count, batches_imported=b_count)


def _clean_table_cell(text: str | None) -> str:
    """清洗 Markdown 表格单元格内容，防止换行和未转义竖线破坏表格结构 (OPT-35)。"""
    if not text:
        return ""
    return str(text).replace("\r\n", " ").replace("\n", " ").replace("|", "\\|").strip()


async def export_devlog_markdown(project: str, max_items: int = 10000) -> str:
    """从数据库生成格式兼容的标准 DEVLOG.md (直接消费 shared.records 通用事实)。"""
    # 兼容 sys_task 与 tasks 编码
    tasks = await db.query_records(project_id=project, dataset_code="sys_task", limit=max_items)
    if not tasks:
        tasks = await db.query_records(project_id=project, dataset_code="tasks", limit=max_items)

    findings = await db.query_records(project_id=project, dataset_code="sys_finding", limit=max_items)
    if not findings:
        findings = await db.query_records(project_id=project, dataset_code="findings", limit=max_items)

    waitings = await db.query_records(project_id=project, dataset_code="sys_waiting", limit=max_items)
    if not waitings:
        waitings = await db.query_records(project_id=project, dataset_code="waitings", limit=max_items)

    batches = await db.query_records(project_id=project, dataset_code="sys_batch", limit=max_items)
    if not batches:
        batches = await db.query_records(project_id=project, dataset_code="batches", limit=max_items)

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

    def _v(obj, key, default=None):
        if isinstance(obj, dict):
            return obj.get(key, default)
        return getattr(obj, key, default)

    for t in tasks:
        p = _v(t, "payload") or {}
        st = _v(t, "status")
        st_icon = "closed" if st == "closed" else st
        commit_str = _clean_table_cell(p.get("commit_hash"))
        title_str = _clean_table_cell(_v(t, "entity_name"))
        proof_or_notes = _clean_table_cell(p.get("proof_link") or p.get("notes"))
        ttype = p.get("task_type", "fix")
        lines.append(f"| {_v(t, 'doc_no')} | {st_icon} | {ttype} | {title_str} | {commit_str} | {proof_or_notes} |")

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
        p = _v(f, "payload") or {}
        res = _clean_table_cell(p.get("resolution"))
        sum_str = _clean_table_cell(p.get("summary") or _v(f, "entity_name"))
        src = p.get("source", "audit")
        sev = p.get("severity", "P2")
        lines.append(f"| {_v(f, 'doc_no')} | {src} | {sev} | {_v(f, 'status')} | {res} | {sum_str} |")

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
        p = _v(w, "payload") or {}
        cat = p.get("category", "user")
        desc_str = _clean_table_cell(p.get("description") or _v(w, "entity_name"))
        lines.append(f"| {_v(w, 'doc_no')} | {cat} | {_v(w, 'status')} | {desc_str} |")

    if batches:
        for b in batches:
            p = _v(b, "payload") or {}
            st = _v(b, "status")
            st_text = "⏸ 停机保存态" if st == "halted" else "✅ 闭环" if st in ("completed", "closed") else "🟢 进行中" if st == "running" else "🔵 规划中"
            lines.extend([
                "",
                "---",
                "",
                f"## [{_v(b, 'doc_no')}] {_v(b, 'entity_name')} — {st_text}",
                "",
            ])
            if p.get("summary"):
                lines.append(f"{p['summary']}")
            if p.get("methodology_notes"):
                lines.append(f"{p['methodology_notes']}")

    return "\n".join(lines) + "\n"
