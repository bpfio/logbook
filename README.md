# Logbook

> **Agent-Native 工业级研发任务台账、根因排查手记与长期记忆中枢**  
> *Industrial-grade Engineering Task Ledger, Root Cause DevLog, and Agent-Native Knowledge Engine.*

---

## 💡 为什么需要 Logbook？

在由人类工程师与多个自主 AI Agent（如 Claude, Cursor, Antigravity）深度协作的现代研发团队中，现有的记录与协同工具有着明显的结构性缺陷：

1. **传统项目管理工具（Jira / GitHub Issues / 飞书项目）**：
   - 过于沉重且偏向人类 GUI 点击交互；
   - 缺乏针对代码底层排查的“故障四要素”强类型约束；
   - Agent 自动化集成开销大，网络通信包袱重。
2. **本地单文件日志（`DEVLOG.md`）**：
   - 多 Agent 并发执行批次任务时极易引发 **Git Merge Conflict**；
   - 随着项目演进，平面文本体积迅速膨胀，Agent 每次通读消耗数千 Context Token；
   - 无法对历史复杂的深层排查经验进行跨项目、毫秒级语义向量检索。
3. **传统社区与论坛系统（BBS / Wiki）**：
   - 领域模型严重错位：充斥着点赞（Upvotes）、盖楼（Comments）、热度算法和聊天室等娱乐冗余；
   - 无法满足结构化 SQL 查询（如：*“列出当前处于 blocked 状态的所有 P1 缺陷”*）。

**Logbook 为解决上述根本痛点而生：彻底剔除一切社区社交负累，以纯粹的“任务状态机 + 根因四要素 + 架构铁律 + 向量混合召回”重塑研发正典。**

---

## 🏛 核心实体设计 (Core Domain Entities)

```
                            ┌────────────────────────────────────────┐
                            │  AI Agent (Cursor / Claude / agy)      │
                            │  & 研发工程师 (CLI / SSH / REST)        │
                            └───────────────────┬────────────────────┘
                                                │ (Native MCP / JSON-RPC)
     ┌──────────────────────────────────────────┴──────────────────────────────────────────┐
     │                             Logbook 研发中枢三大正典                                 │
     ├─────────────────────────┬──────────────────────────┬────────────────────────────────┤
     │   1. 任务台账 (tasks)    │   2. 排查手记 (devlogs)   │   3. 工程铁律 (rules)          │
     ├─────────────────────────┼──────────────────────────┼────────────────────────────────┤
     │ · 任务编号 (F14/W12)    │ · 归属任务 (task_id)      │ · 规则名称 (SSOT)              │
     │ · 状态机 (planned~closed)│ · 故障现象 (problem)     │ · 核心原则 (summary)           │
     │ · 优先级 (P1/P2/P3)     │ · 根因定位 (root_cause)  │ · 错误示范 (bad_practice)      │
     │ · 关联批次 (batch_id)    │ · 解决方案 (solution)    │ · 正确做法 (good_practice)     │
     │ · Commit 证据指针       │ · 512 维向量 (pgvector)  │ · 边界约束 (constraints)       │
     └─────────────────────────┴──────────────────────────┴────────────────────────────────┘
                                                │
                             ┌──────────────────┴──────────────────┐
                             │  底座：PostgreSQL 17 + pgvector     │
                             │  向量：讯飞星火 MaaS / FastEmbed 本地│
                             └─────────────────────────────────────┘
```

### 1. 任务台账 (`tasks`)
* **状态机流转**：`planned`（计划）→ `running`（进行中）→ `blocked`（阻塞待决）→ `closed`（已办结）；
* **证据锚定**：办结必须附带提交的 Commit Hash 与验证复现证据指针；
* **并发安全**：数据库行级事务，多 Agent 并行开发零代码冲突。

### 2. 排查手记 (`devlogs`)
* **根因四要素**：严格要求录入“故障现象 (Problem)”、“根因剖析 (Root Cause)”、“修复方案 (Solution)”、“验证结果 (Evidence)”；
* **语义长期记忆**：自动提取 512 维向量，跨项目混合语义检索，让后续 Agent 毫秒级复用前人经验。

### 3. 工程铁律 (`rules`)
* **单一事实源 (SSOT)**：统一定义反面模式（Bad Practice）与最佳实践（Good Practice），开工前自动对齐。

---

## 🚀 接入协议与交互方式

1. **Model Context Protocol (MCP)**：
   * 原生提供 Stdio / SSH JSON-RPC 2.0 管道；
   * 开箱即用的高频工具：
     * `task_upsert`：原子登记/推进任务状态
     * `task_query`：按状态、优先级多维查询当前阻塞与活跃项
     * `devlog_record`：结构化沉淀排查手记（自动计算向量）
     * `devlog_search`：全文 + 向量语义混合召回
     * `rule_query`：开工前规程与架构红线对齐
2. **研发人员极简 CLI**：
   * `logbook status`：即时打印未结任务看板
   * `logbook show <task_id>`：查阅指定任务详情与排查报告
3. **FastAPI REST 接口**：
   * 全自动 OpenAPI / Swagger 交互契约，便于 Webhook 与 CI/CD 集成。

---

## 📄 研发正典纪律

本项目严格执行 [`AGENTS.md`](./AGENTS.md) 与 [`docs/DEVLOG.md`](./docs/DEVLOG.md) 中规定的研发正典纪律。所有 Agent 开工前必须先行通读台账。

---

## 📜 许可证

[Apache-2.0 License](./LICENSE)
