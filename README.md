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

## 🏛 核心架构与双平面隔离 (Dual-Plane Architecture)

```
                            ┌────────────────────────────────────────┐
                            │  AI Agent (Cursor / Claude / agy)      │
                            │  & 研发工程师 (CLI / SSH / FastMCP)     │
                            └───────────────────┬────────────────────┘
                                                │ (MCP 2.x / JSON-RPC 2.0)
     ┌──────────────────────────────────────────┴──────────────────────────────────────────┐
     │                             Logbook 研发中枢三大正典                                 │
     ├─────────────────────────┬──────────────────────────┬────────────────────────────────┤
     │   1. 任务台账 (tasks)    │   2. 排查手记 (devlogs)   │   3. 工程铁律 (rules)          │
     ├─────────────────────────┼──────────────────────────┼────────────────────────────────┤
     │ · 任务编号 (F14/W12)    │ · 归属任务 (task_id)      │ · 规则名称 (SSOT)              │
     │ · 状态机 (planned~closed)│ · 故障现象 (problem)     │ · 核心原则 (summary)           │
     │ · 优先级 (P0~P3)        │ · 根因定位 (root_cause)  │ · 错误示范 (bad_practice)      │
     │ · 关联批次 (batch_id)    │ · 解决方案 (solution)    │ · 正确做法 (good_practice)     │
     │ · Commit 证据指针       │ · 512 维向量 (pgvector)  │ · 边界约束 (constraints)       │
     └─────────────────────────┴──────────────────────────┴────────────────────────────────┘
                                                │
                ┌───────────────────────────────┴───────────────────────────────┐
                │             双平面隔离底座 (PostgreSQL 18.6 GA + pgvector)      │
                ├───────────────────────────────┬───────────────────────────────┤
                │     共享控制面 (shared)       │     多租户数据面 (proj_{name}) │
                ├───────────────────────────────┼───────────────────────────────┤
                │ · shared.projects (注册中心)  │ · proj_brix.tasks / devlogs   │
                │ · shared.rules (跨项目铁律)    │ · proj_logbook.tasks / devlogs│
                │ · 全局向量索引 (HNSW 512维)    │ · 独立事务锁，物理模式隔离    │
                └───────────────────────────────┴───────────────────────────────┘
```

### 1. 任务台账 (`tasks`)
* **状态机流转**：`planned`（计划）→ `running`（进行中）→ `blocked`（阻塞待决）→ `closed`（已办结）/ `wontfix`；
* **证据锚定**：办结状态强制要求提交 `commit_hash` 或 `proof_link` 证据指针；
* **耗时审计**：自动基于高精度授时计算任务耗时 `duration_seconds`。

### 2. 排查手记 (`devlogs`)
* **根因四要素**：严格要求录入“故障现象 (Problem)”、“根因剖析 (Root Cause)”、“修复方案 (Solution)”、“验证证据 (Evidence)”；
* **语义长期记忆**：自动提取 512 维向量与全文倒排索引（pgvector + tsvector），跨项目混合召回。

### 3. 工程铁律 (`rules`)
* **单一事实源 (SSOT)**：统一定义反面模式（Bad Practice）与最佳实践（Good Practice），开工前自动对齐。

---

## 🛡 工业级多项目隔离与交互协商引擎

### 1. 强制显式项目传参 (Mandatory Project Argument)
所有 MCP 工具调用均将 `project: str` 作为**第一必填参数**，坚决消灭单项目默认回退或 Agent 隐式脑补。

### 2. 三级交互协商自愈引擎 (Interactive Negotiation Engine)
当 Agent 或工程师意外输入拼写错误的项目名（例如将 `brix` 错敲为 `briz`）时：
1. **即时模糊计算**：系统通过 `difflib` 计算 Levenshtein 相似度并提取相似合法项目候选集 `['brix']`；
2. **物理工作区感知**：探测当前运行目录的 git remote URL / 工作区根路径；
3. **结构化协商响应**：通过 MCP 2.x `isError=True` 返回结构化协商报错与修复指导，驱动 Agent 0 人工干预自愈纠正：
   ```json
   {
     "status": "negotiation_required",
     "error": "Project 'briz' not found.",
     "suggestions": ["brix"],
     "workspace_hint": "Detected workspace matches existing project 'brix'. Did you mean 'brix'?",
     "action": "Please re-call the tool with the correct project name."
   }
   ```
4. **越权阻断防护**：若 Agent 试图跨物理工作区篡改非所属项目数据，坚决抛出 `PermissionError` 终止执行。

### 3. 隐私脱敏与授时同步保底
* **脱敏保底**：内置 `Sanitizer` 自动拦截公网 IP 与私网 IPv4/IPv6，统一转换为 RFC 5737 (`192.0.2.x`) 与 RFC 3849 (`2001:db8::x`) 保留地址，自动擦除 Bearer / API Token；
* **高精度授时**：内置 SNTP 客户端与北京时间（UTC+8）自动校准，防止分布式 Agent 时钟漂移。

---

## 🚀 MCP 2.x 全维原语支持

Logbook 原生实现全套 MCP 2.x 规范，兼备工具、上下文资源与规程提示词模板：

### 1. 核心 Tools

**台账类**
* `task_upsert(project, ...)`：原子登记/推进任务状态机
* `tasks_bulk_upsert(project, tasks, ...)`：单事务批量落库
* `task_query / task_detail`：多维查询任务列表与详情
* `finding_record / finding_query`：缺陷登记与查询（`findings_bulk_upsert` 批量原语将随 0.2.0 上线）
* `waiting_record / waiting_query`：待决/阻塞项登记与查询（`waitings_bulk_upsert` 批量原语将随 0.2.0 上线）
* `batch_upsert / batch_query`：研发批次演进记录

**手记与规程类**
* `devlog_record(project, ...)`：结构化录入排查手记（四要素+自动向量化，幂等去重）
* `devlog_search(project, query, ...)`：全文 + 向量语义混合召回（结果按笔去重）
* `rule_query`：读取研发规程与架构红线
* `project_list / project_init`：项目注册中心与健康状态

**效率端点（0.2.0 将上线）**
* `logbook_brief(project)`：一次调用返回全项目 open tasks/findings/waitings + running 批次 + 最近 devlog 标题的 brief digest，单包目标 ≤2K token

**响应与错误契约（0.2.0）**
* 写响应瘦身：`*_record` / `*_upsert` 缺省仅回 `{ok, id, status}`，`full=true` 才回显全对象
* 字段投影：查询工具支持 `fields` 参数，缺省精简列集
* 结构化错误：全部工具异常统一返回 `{isError, error_type, detail}`（含 `TASK_NOT_FOUND` 预检），杜绝 "Error executing tool X" 裸包装

### 2. 上下文 Resources (只读 URI)
* `logbook://{project}/tasks/active`：该项目当前活跃/进行中任务
* `logbook://{project}/waitings/open`：该项目当前待决/阻塞项
* `logbook://rules/engineering`：全团队工程铁律单一事实源

### 3. 规程 Prompts (模板指令)
* `/start_batch`：开工前通读任务台账与阻塞项的标准指导模板
* `/record_devlog`：规范化填写排查手记四要素的辅助模板

---

## 💻 极简研发 CLI

```bash
# 1. 运行系统环境自检 (DB 连接、时区、NTP 漂移、Docker 内存限制)
logbook doctor

# 2. 查阅指定项目任务看板
logbook status brix

# 3. 查阅指定任务详情
logbook show brix F14

# 4. 双向无损转换: 从 Markdown 导入到 Logbook 数据库
logbook import brix docs/DEVLOG.md

# 5. 双向无损转换: 从 Logbook 数据库导出为 Markdown
logbook export brix docs/DEVLOG_EXPORT.md

# 6. 启动 FastMCP 服务 (Stdio 管道)
logbook mcp
```

---

## ⚡ 极速启动与配置

### 1. 启动轻量 PostgreSQL 18 底座 (内存硬限制 80MB)

> QNAP / NAS 生产部署与升级（PG 备份、镜像重建、SQL 迁移、MCP 回归、回滚）
> 见 [`deploy/qnap/DEPLOY.md`](./deploy/qnap/DEPLOY.md)；服务编排模板见
> `deploy/qnap/compose.yaml.example`（密钥经 `.env` 注入，不入仓库）。
```bash
docker compose up -d
```
> 实测容器物理内存常驻仅 ~42MB，针对 2C2G 边缘云及家庭 NAS 深度优化。

### 2. 配置 MCP 客户端 (以 Antigravity / Claude Desktop 为例)
在 `~/.gemini/antigravity-cli/mcp.json` 或 `claude_desktop_config.json` 中配置：
```json
{
  "mcpServers": {
    "logbook": {
      "command": "/path/to/logbook/.venv/bin/logbook",
      "args": ["mcp"],
      "env": {
        "DATABASE_URL": "postgresql://postgres:postgres@127.0.0.1:5432/logbook",
        "APP_ENV": "production"
      }
    }
  }
}
```

---

## 📄 研发正典纪律

本项目严格执行 [`AGENTS.md`](./AGENTS.md) 与 [`docs/DEVLOG.md`](./docs/DEVLOG.md) 中规定的研发正典纪律。所有 Agent 开工前必须先行通读台账。

---

## 📜 许可证

[Apache-2.0 License](./LICENSE)
