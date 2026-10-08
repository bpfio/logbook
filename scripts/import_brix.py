"""Brix DEVLOG.md 结构化对齐与向量化灌库脚本

功能:
1. 解析 /home/yupeng/brix/docs/DEVLOG.md
2. 灌库 brix.batches (4 大批次: DEV-2026-10-07-02 停机保存态, DEV-2026-10-07-01 等)
3. 灌库 brix.tasks (20 项任务, 绑定 batch_id, 状态对齐 F19 blocked, 责任人, 证据指针)
4. 灌库 brix.findings (91 项发现, 级别 P1~P3, 状态 open/infix/fixed/wontfix/blocked)
5. 灌库 brix.waitings (14 项待办/待用户裁决)
6. 提炼 4 篇深度排障手记注入 shared.devlogs, 调用讯飞星火 MaaS API 生成 512 维向量
"""

import sys
import os
import asyncio
from pathlib import Path

# 添加 src 到路径
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from logbook.db import db
from logbook.models import (
    Task, TaskStatus, TaskPriority, TaskType,
    Finding, FindingStatus, FindingSeverity,
    Waiting, WaitingStatus, WaitingCategory,
    Batch, BatchStatus,
    DevLog, DevLogVisibility
)
from logbook.converter import import_devlog_markdown
from logbook.vector import get_embedding


DEVLOG_PATH = Path("/home/yupeng/brix/docs/DEVLOG.md")

# 责任人映射表 (源自 CONTROL_LOG.md)
AGENT_MAP = {
    "F1": "agent_2feb5b8b",
    "F2": "agent_fa9b3938",
    "F3": "agent_e3a8da07",
    "F4": "agent_4e7e6c64",
    "F5": "agent_6fb73671",
    "W6": "agent_192a22d3",
    "W7": "agent_w7",
    "W8": "agent_w8",
    "W9": "agent_w9",
    "W10": "agent_w10",
    "W12": "agent_w12",
    "F11": "agent_f11",
    "F13": "agent_f13",
    "F14": "night10/cli-fix-14",
    "F15": "night10/cli-fix-15",
    "F16": "night10/cli-fix-16",
    "F17": "night10/cli-fix-17",
    "F18": "night10/cli-fix-18",
    "F19": "night10/cli-fix-19",
    "F20": "night10/cli-fix-20",
}

DEVLOGS_TO_RECORD = [
    {
        "task_id": "F14",
        "title": "XDP reconcile EBUSY 根因修复与 wan2 抖动零告警机理调查",
        "author": "night10/cli-fix-14",
        "problem": "r166 部署窗 journal 报错 xdp reconcile failed (attach proceeds) ... detach ledger-owned prog ... device or resource busy 达 4 次；且 wan2 物理口发生抖动时管理面零告警。",
        "root_cause": "1. cilium/ebpf v0.22 的 link.AttachXDP 走 BPF_LINK_CREATE (bpf_link) 系统调用，注释误称走 netlink；2. netlink IFLA_XDP_FD=-1 摘除手段对 bpf_link 在挂程序结构性无效，内核恒返回 EBUSY；3. 启动窗 PPPoE 会话建立重跑挂载循环时，reconcile 读到本会话新挂的 prog 并尝试 netlink 摘旧必然冲突；4. wan2 告警豁免逻辑无代码实现，成功投递即被 journal 静默 (F-322⑤ 翻案)。",
        "solution": "1. 在 pkg/ebpf/reconcile.go 新增 ReconcileXDPExcluding 与 SessionXDPProgIDs 会话豁免集；对账读到豁免集 prog id 时直接跳过摘除；2. 计划外端口真摘除委托给紧随的 pruneXDPLinks 关 link fd 完成；3. 同步修正 docs/CLI.md §6 行为规范。",
        "evidence": "pkg/ebpf 与 pkg/engine 全量单元测试 21 包全绿；dummy netns live 实测 (setpriv 去 sys_admin 定名 EPERM 走台账路径) 验证豁免后 Info 'session-owned program in place, skip' 且挂载原样保持，零噪音零残留。",
        "tags": ["ebpf", "xdp", "bpf_link", "ebusy", "network", "wan"]
    },
    {
        "task_id": "F17",
        "title": "Telegram Bot Token 泄漏脱敏与错误输出清洗",
        "author": "night10/cli-fix-17",
        "problem": "F16 演练中发现：当外发 HTTP 请求异常时，错误信息中直接暴露完整的 Telegram Bot Token 明文，极易泄露至审计日志与屏幕终端。",
        "root_cause": "错误拼接逻辑直接格式化原始请求 URL (如 /bot<TOKEN>/sendMessage)，导致底层 HTTP client 抛出的异常字符串携带了凭据原文，缺乏应用层脱敏拦截防线。",
        "solution": "1. 确立统一的 redactedError 范式与 URL 凭据掩码函数 sanitizeURL；2. 在所有网络出口与日志打印前强制拦截并替换私密 token 为 <REDACTED>；3. 统一错误包装逻辑。",
        "evidence": "F17 交付 commit ce0fb35 验证：全量回归测试中 bot_token 泄漏脱敏实证 token 计数严格归零，日志与返回体中仅出现掩码标识。",
        "tags": ["security", "redaction", "credential", "telegram", "token"]
    },
    {
        "task_id": "F18",
        "title": "Diff 凭据脱敏、Private Key 敏感集过滤与 LAN 重编址往返不对称",
        "author": "night10/cli-fix-18",
        "problem": "配置差异对比工具 (brix diff) 打印输出中出现明文 private_key；且 LAN 重编址修改后逆向对比存在字段序列化往返不对称缺陷。",
        "root_cause": "1. diff 比较引擎未挂载凭据脱敏遮蔽过滤器；2. LAN 重编址联动原语未同时更新关联的 DHCP/DNS 动态租期与序列化映射，导致模型反序列化丢失字段。",
        "solution": "1. 建立 private_key / auth_token 敏感键掩码集，diff 默认开启 --mask-secrets；2. 实施方案 A 联动重编址原子事务，补齐双向序列化对齐逻辑。",
        "evidence": "演练 6/6 通过，交付 commit 93fedec，diff --offline 输出中所有私钥字段均显示为掩码占位，重编址往返对比 100% 对称。",
        "tags": ["security", "diff", "private_key", "network", "lan"]
    },
    {
        "task_id": "F-320",
        "title": "PPPoE 上游 14B 报文截断排障机理与 MTU/MRU/MSS-Clamp 方案裁决",
        "author": "agy",
        "problem": "PPPoE 接入链路在传输大包时偶发上游 14B 报文截断丢包，导致特定大流量 TCP 连接卡顿挂死 (F-320)。",
        "root_cause": "上游宽带接入服务器 (BAS) 存在 14 字节额外帧头封装开销，未扣除额外 VLAN/PPPoE 开销导致物理链路有效 MTU 缩水至 1486 以下，超出部分被强制截断。",
        "solution": "形成三选一方案矩阵待用户裁决：1. 推动运营商上游整改；2. 将本地 ppp mtu/mru 调低至 1480；3. 在防火墙入口启用 TCP MSS Clamping (clamp-mss-to-pmtu) 自动协商分片大小。",
        "evidence": "流量嗅探抓包取证已归档，已在待办台账建档 WAIT-F320，待人类专家决策落实。",
        "tags": ["network", "pppoe", "mtu", "mss", "clamp"]
    }
]


async def run_import(target: str = "local"):
    print(f"=== 开始执行 Brix DEVLOG 结构化对齐与向量化灌库 (目标: {target}) ===")

    if not DEVLOG_PATH.exists():
        print(f"错误: 未找到 {DEVLOG_PATH}")
        sys.exit(1)

    # 1. 确保项目 Schema
    print("1. 初始化 brix 项目 Schema...")
    await db.ensure_project("brix")

    # 2. 读入并解析 Markdown
    print(f"2. 逆向工程解析 {DEVLOG_PATH}...")
    content = DEVLOG_PATH.read_text(encoding="utf-8")
    summary = await import_devlog_markdown("brix", content)
    print(f"   初步解析完成: 批次 {summary.batches_imported} 项, 任务 {summary.tasks_imported} 项, "
          f"发现 {summary.findings_imported} 项, 待办 {summary.waitings_imported} 项")

    # 3. 任务精细化对齐 (责任人锁、批次绑定、状态闭环)
    print("3. 精细化对齐任务台账 (绑定 DEV-2026-10-07-02、分配责任人、对齐 F19 blocked)...")
    tasks = await db.query_tasks("brix", limit=200)
    for t in tasks:
        # 分配责任人
        if t.id in AGENT_MAP:
            t.assignee = AGENT_MAP[t.id]
        # 强制挂载批次
        t.batch_id = "DEV-2026-10-07-02"
        # F19 状态核准
        if t.id == "F19":
            t.status = TaskStatus.BLOCKED
            t.notes = "用户令停机保存 — WIP @ origin/night10/cli-fix-19 (f19_drill_test 骨架+部分修复), 复作入口=该 commit + cli-drill-f15.md P3×8 清单"
        await db.upsert_task("brix", t)
    print(f"   已更新 {len(tasks)} 项任务的精细属性。")

    # 4. 深度排障手记沉淀与 512 维向量生成 (P0-1: 统一走 record_devlog upsert 路径，消灭旁路)
    print("4. 沉淀核心排障手记并计算讯飞星火 512 维向量...")
    for item in DEVLOGS_TO_RECORD:
        # 幂等查重: 同 (project, task_id, title) 已存在且四要素未变 → 复用向量缓存，不重复嵌入
        existing = await db.find_existing_devlog("brix", task_id=item["task_id"], title=item["title"])
        embedding = None
        if existing is None or any(
            existing.get(k) != item[k]
            for k in ("title", "problem", "root_cause", "solution", "evidence")
        ):
            full_text = f"{item['title']} {item['problem']} {item['root_cause']} {item['solution']}"
            embed_res = await get_embedding(full_text)
            embedding = embed_res.embedding
            vec_source = embed_res.source
        else:
            vec_source = "cached_skip"

        devlog = DevLog(
            project_id="brix",
            task_id=item["task_id"],
            title=item["title"],
            author=item["author"],
            problem=item["problem"],
            root_cause=item["root_cause"],
            solution=item["solution"],
            evidence=item["evidence"],
            visibility=DevLogVisibility.PUBLIC_SAFE,
            tags=item["tags"],
            embedding=embedding
        )
        saved = await db.record_devlog(devlog)
        print(f"   ✔ 手记 upsert [{saved.action}]: ID={saved.id} [{saved.task_id}] {saved.title[:30]} (向量源: {vec_source})")

    print("\n=== 导入与对齐圆满完成！ ===")


if __name__ == "__main__":
    asyncio.run(run_import())
