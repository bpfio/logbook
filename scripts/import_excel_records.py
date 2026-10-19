#!/usr/bin/env python3
"""通用事实数据导入脚本 (支持 CSV / TSV / Excel XLSX 极速灌库)

特性:
- 零外部繁重依赖 (内置 zipfile + xml 流式解析 XLSX，兼容 CSV utf-8/gbk/gb18030)
- 强脊梁与软肋骨自动分流:
  - 核心属性 (doc_no, entity_name, amount, currency, event_date, status) 写入强类型字段
  - 动态属性 (其余 10~100 个业务列) 自动打包为 payload JSONB
- 基于数据集别名映射 (alias_map) 自动对齐多版本中英文表头
- 批量流式写入 (默认 1,000 行/事务批次)，输出全过程耗时与财务校验和 (SUM amount Checksum)
"""

import sys
import os
import re
import csv
import time
import zipfile
import argparse
import asyncio
from datetime import datetime, date
from decimal import Decimal
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from logbook.db import db
from logbook.models import DataRecord
from logbook.normalizer import normalize_record_status


# 常见字段名探测启发规则
CANDIDATE_DOC_NO = {"doc_no", "申报号", "申报单号", "单据号", "发票号", "业务单号", "报关单号", "id", "单号", "编号", "流水号"}
CANDIDATE_ENTITY = {"entity_name", "往来单位", "付款人", "收款人", "境内收发货人", "申报单位", "境外付款人", "企业名称", "客户名称", "对手方", "repo", "repository", "unit"}
CANDIDATE_AMOUNT = {"amount", "结汇金额", "金额", "收汇金额", "申报金额", "核销金额", "总价", "成交总价", "数值", "值", "score", "value", "count"}
CANDIDATE_CURRENCY = {"currency", "币种", "结汇币种", "收汇币种", "币别", "单位"}
CANDIDATE_DATE = {"event_date", "业务日期", "申报日期", "收汇日期", "开票日期", "日期", "发生日期", "date", "time"}
CANDIDATE_STATUS = {"status", "状态", "审核状态", "单据状态"}


def parse_xlsx_cells(file_path: str) -> list[list[str]]:
    """轻量级流式解析 XLSX 文件首个工作表 (零依赖，基于标准库 zipfile + xml)。"""
    with zipfile.ZipFile(file_path, "r") as z:
        # 1. 解析共享字符串表 (sharedStrings.xml)
        shared_strings = []
        if "xl/sharedStrings.xml" in z.namelist():
            with z.open("xl/sharedStrings.xml") as f:
                tree = ET.parse(f)
                root = tree.getroot()
                # 命名空间处理
                ns = {"ns": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
                for si in root.findall(".//ns:si", ns) or root.findall(".//si"):
                    texts = [t.text or "" for t in (si.findall(".//ns:t", ns) or si.findall(".//t"))]
                    shared_strings.append("".join(texts))

        # 2. 定位工作表 1
        sheet_path = "xl/worksheets/sheet1.xml"
        if sheet_path not in z.namelist():
            sheets = [n for n in z.namelist() if n.startswith("xl/worksheets/sheet") and n.endswith(".xml")]
            if not sheets:
                raise ValueError(f"XLSX 文件中未找到任何工作表: {file_path}")
            sheet_path = sorted(sheets)[0]

        rows: list[list[str]] = []
        with z.open(sheet_path) as f:
            for event, elem in ET.iterparse(f, events=("end",)):
                tag = elem.tag.split("}")[-1]
                if tag == "row":
                    row_data: list[str] = []
                    col_idx_expected = 1
                    for c in elem.findall(".//c") or elem.findall(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}c"):
                        cell_ref = c.attrib.get("r", "")
                        # 列字母转索引 (如 A=1, B=2, AA=27)
                        m = re.match(r"([A-Z]+)(\d+)", cell_ref)
                        if m:
                            col_letters = m.group(1)
                            actual_col = 0
                            for ch in col_letters:
                                actual_col = actual_col * 26 + (ord(ch) - ord("A") + 1)
                            # 补齐跳过的空单元格
                            while col_idx_expected < actual_col:
                                row_data.append("")
                                col_idx_expected += 1

                        val_type = c.attrib.get("t")
                        v_tag = c.find(".//v") or c.find(".//{http://schemas.openxmlformats.org/spreadsheetml/2006/main}v")
                        cell_text = ""
                        if v_tag is not None and v_tag.text is not None:
                            val_str = v_tag.text.strip()
                            if val_type == "s" and val_str.isdigit():
                                s_idx = int(val_str)
                                if s_idx < len(shared_strings):
                                    cell_text = shared_strings[s_idx]
                            else:
                                cell_text = val_str
                        row_data.append(cell_text)
                        col_idx_expected += 1

                    if any(cell.strip() for cell in row_data):
                        rows.append(row_data)
                    elem.clear()

        return rows


def read_file_records(file_path: str) -> list[dict[str, str]]:
    """读取 CSV、TSV 或 XLSX 并返回表头字典列表。"""
    p = Path(file_path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    ext = p.suffix.lower()
    if ext in (".xlsx", ".xlsm"):
        raw_rows = parse_xlsx_cells(str(p))
        if not raw_rows:
            return []
        headers = [h.strip() for h in raw_rows[0]]
        records = []
        for r in raw_rows[1:]:
            d = {}
            for i, h in enumerate(headers):
                if h:
                    d[h] = r[i].strip() if i < len(r) else ""
            if any(d.values()):
                records.append(d)
        return records

    # CSV / TSV 读取 (自动测试 utf-8, gbk, gb18030)
    for enc in ("utf-8-sig", "utf-8", "gb18030", "gbk"):
        try:
            with open(p, "r", encoding=enc, errors="strict") as f:
                sample = f.read(4096)
                delimiter = "\t" if "\t" in sample and "," not in sample else ","
                f.seek(0)
                reader = csv.DictReader(f, delimiter=delimiter)
                results = []
                for row in reader:
                    cleaned_row = {str(k).strip(): str(v).strip() for k, v in row.items() if k is not None}
                    if any(cleaned_row.values()):
                        results.append(cleaned_row)
                return results
        except (UnicodeDecodeError, csv.Error):
            continue

    # 回退容错读取
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.DictReader(f)
        return [{str(k).strip(): str(v).strip() for k, v in row.items() if k is not None} for row in reader]


def parse_date_value(val: Any) -> str:
    """归一化日期为 YYYY-MM-DD 字符串。"""
    if not val:
        return date.today().isoformat()
    s = str(val).strip()
    # 纯 8 位数字: 20261019
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    # 替换斜杠
    s = s.replace("/", "-")
    m = re.match(r"^(\d{4})[-.](\d{1,2})[-.](\d{1,2})", s)
    if m:
        y, month, d = m.groups()
        return f"{y}-{int(month):02d}-{int(d):02d}"
    return date.today().isoformat()


def parse_amount_value(val: Any) -> Decimal:
    """提取金额/数值高精度 Decimal。"""
    if val is None:
        return Decimal("0.0000")
    s = str(val).replace(",", "").replace("¥", "").replace("$", "").replace(" ", "").strip()
    if not s:
        return Decimal("0.0000")
    try:
        return Decimal(s)
    except Exception:
        # 正则提取首个浮点数字
        m = re.search(r"[-+]?\d*\.?\d+", s)
        if m:
            try:
                return Decimal(m.group(0))
            except Exception:
                pass
        return Decimal("0.0000")


async def async_main():
    parser = argparse.ArgumentParser(description="Logbook 通用事实数据极速导入工具")
    parser.add_argument("--file", "-f", required=True, help="输入数据文件路径 (CSV / TSV / XLSX)")
    parser.add_argument("--dataset", "-d", required=True, help="数据集编码 (如 safe_forex, oss_benchmark)")
    parser.add_argument("--project", "-p", required=True, help="归属项目代号 (如 brix, ts, raw)")
    parser.add_argument("--batch-id", "-b", help="导入批次号 (缺省自动生成)")
    parser.add_argument("--batch-size", type=int, default=1000, help="DB 批处理大小 (缺省 1000)")
    parser.add_argument("--doc-col", help="单据号列名 (可自动推断)")
    parser.add_argument("--entity-col", help="往来单位列名 (可自动推断)")
    parser.add_argument("--amount-col", help="核心数值/金额列名 (可自动推断)")
    parser.add_argument("--currency-col", help="币种/量纲列名 (可自动推断)")
    parser.add_argument("--date-col", help="业务发生日期列名 (可自动推断)")
    parser.add_argument("--status-col", help="状态列名 (可自动推断)")
    parser.add_argument("--dry-run", action="store_true", help="演练校验模式，不写入数据库")

    args = parser.parse_args()

    file_path = Path(args.file).resolve()
    if not file_path.exists():
        print(f"❌ 错误: 文件未找到: {file_path}")
        sys.exit(1)

    t0 = time.time()
    print(f"📂 正在读取数据文件: {file_path} ...")
    raw_rows = read_file_records(str(file_path))
    if not raw_rows:
        print("⚠️ 警告: 数据文件为空，0 行记录！")
        return

    print(f"✅ 文件解析成功: 共 {len(raw_rows):,} 行数据。")

    # 连接数据库并获取数据集字典规范
    alias_map = {}
    try:
        ds_meta = await db.get_dataset(args.dataset)
        alias_map = ds_meta.get("alias_map", {}) if ds_meta else {}
    except Exception as e:
        if args.dry_run:
            print(f"ℹ️ [DRY-RUN] 数据库暂未连接，使用本地通用启发规则推断列映射。")
        else:
            raise

    # 列映射自动侦测
    first_row = raw_rows[0]
    headers = list(first_row.keys())

    def detect_col(explicit: str | None, candidates: set[str], canonical_name: str) -> str | None:
        if explicit and explicit in headers:
            return explicit
        # 1. 尝试从 alias_map 反查
        for h in headers:
            if alias_map.get(h) == canonical_name or alias_map.get(h.lower()) == canonical_name:
                return h
        # 2. 尝试候选词集
        for h in headers:
            h_clean = h.strip().lower()
            if h_clean in candidates or any(c in h_clean for c in candidates):
                return h
        return None

    col_doc = detect_col(args.doc_col, CANDIDATE_DOC_NO, "doc_no")
    col_entity = detect_col(args.entity_col, CANDIDATE_ENTITY, "entity_name")
    col_amount = detect_col(args.amount_col, CANDIDATE_AMOUNT, "amount")
    col_currency = detect_col(args.currency_col, CANDIDATE_CURRENCY, "currency")
    col_date = detect_col(args.date_col, CANDIDATE_DATE, "event_date")
    col_status = detect_col(args.status_col, CANDIDATE_STATUS, "status")

    if not col_doc:
        print(f"⚠️ 未能自动探测到 doc_no 列，将使用行号作为流水单号！现有表头: {headers}")

    print("📊 字段映射识别结果:")
    print(f"  - 单据单号 (doc_no):      [{col_doc or '<行号自动生成>'}]")
    print(f"  - 往来单位 (entity_name): [{col_entity or '<无>'}]")
    print(f"  - 核心数值 (amount):      [{col_amount or '<0.0000>'}]")
    print(f"  - 币种量纲 (currency):    [{col_currency or 'CNY'}]")
    print(f"  - 业务日期 (event_date):  [{col_date or '<当天>'}]")
    print(f"  - 状态标识 (status):      [{col_status or 'recorded'}]")

    batch_id = args.batch_id or f"imp_{datetime.now().strftime('%Y%m%d%H%M')}_{file_path.stem[:16]}"
    spine_cols = {c for c in (col_doc, col_entity, col_amount, col_currency, col_date, col_status) if c}

    records_to_insert: list[DataRecord] = []
    total_amount_sum = Decimal("0.0000")

    for idx, row in enumerate(raw_rows, start=1):
        doc_no = row.get(col_doc, "").strip() if col_doc else f"{batch_id}#{idx:06d}"
        if not doc_no:
            doc_no = f"{batch_id}#{idx:06d}"

        entity_name = row.get(col_entity, "").strip() if col_entity else None
        amt = parse_amount_value(row.get(col_amount)) if col_amount else Decimal("0.0000")
        total_amount_sum += amt

        curr = row.get(col_currency, "").strip().upper() if col_currency else "CNY"
        if not curr:
            curr = "CNY"

        ev_date = parse_date_value(row.get(col_date)) if col_date else date.today().isoformat()
        status_raw = row.get(col_status, "recorded") if col_status else "recorded"
        status = normalize_record_status(status_raw)

        # 软肋骨: 其余所有非脊柱字段打包为 payload
        payload = {k: v for k, v in row.items() if k not in spine_cols and v != ""}

        rec = DataRecord(
            project_id=args.project,
            dataset_code=args.dataset,
            batch_id=batch_id,
            doc_no=doc_no,
            entity_name=entity_name,
            amount=amt,
            currency=curr,
            event_date=ev_date,
            status=status,
            payload=payload,
        )
        records_to_insert.append(rec)

    print(f"\n📦 校验完毕: 待导入数据行: {len(records_to_insert):,} 条 | 财务金额校验和: {total_amount_sum:,.4f}")

    if args.dry_run:
        print("🔍 [DRY-RUN] 演练模式结束，未向数据库写入数据。")
        return

    # 分批入库
    batch_size = max(10, args.batch_size)
    total_imported = 0
    t_start_db = time.time()

    for i in range(0, len(records_to_insert), batch_size):
        chunk = records_to_insert[i:i + batch_size]
        cnt = await db.bulk_import_records(chunk)
        total_imported += cnt
        pct = (total_imported / len(records_to_insert)) * 100
        print(f"  ⚡ 批次进度: {total_imported:,} / {len(records_to_insert):,} ({pct:.1f}%) ...", end="\r")

    t_end = time.time()
    db_duration = t_end - t_start_db
    total_duration = t_end - t0
    qps = total_imported / db_duration if db_duration > 0 else 0

    print(f"\n🎉 导入大功告成！")
    print(f"  - 成功入库:       {total_imported:,} 条")
    print(f"  - 归属批次:       {batch_id}")
    print(f"  - 财务校验和:     {total_amount_sum:,.4f}")
    print(f"  - 全程总耗时:     {total_duration:.3f}s")


def main():
    async def _run():
        try:
            await async_main()
        finally:
            try:
                await db.close()
            except Exception:
                pass
    asyncio.run(_run())


if __name__ == "__main__":
    main()
