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
* **语义长期记忆**：自动提取 768 维语义向量与全文倒排索引（pgvector + tsvector），跨项目混合召回。

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

## 🚀 MCP 2.x 全维原语支持 (14 大核心正典工具)

Logbook 原生实现全套 MCP 2.x 规范，彻底摒弃过度工程化，全面收敛至 14 个高内聚核心正典原语：

### 1. 通用事实表操作原语 (Universal Fact Engine)
* `record_upsert(project, dataset_code, doc_no, payload, ...)`：原子登记/更新业务事实单据（动态 JSON Schema 强校验，自动生成 768 维语义向量）
* `records_bulk_upsert(project, records)`：单事务批量原子入库（复合键 `(dataset_code, doc_no)` 幂等更新）
* `record_query(project, dataset_code, status, doc_no, ...)`：结构化多维事实查询（支持状态数组、时间范围、金额范围与字段投影）
* `record_search(project, query, dataset_code, limit, min_score)`：768 维语义向量 + GIN 倒排全文混合检索（带相关度评分与截断）
* `record_stats(project, dataset_code, status, ...)`：单表直查条件聚合统计（总数、总金额、状态分布等，时延 ~3ms）

### 2. 元数据字典与项目租户治理
* `dataset_manage(action, dataset_code, name, schema, ...)`：业务数据集元数据与 JSON Schema 契约治理 (`register`, `update_schema`, `list`)
* `project_manage(action, repo, title, description, ...)`：项目租户全生命周期管理 (`init`, `update`, `list`, `query`)
* `group_manage(action, group_name, agent_name, project, ...)`：RBAC 多用户与群组权限管理 (`upsert`, `grant`, `revoke`, `add_member`, `remove_member`, `query_groups`, `query_principals`)

### 3. 智能体协同通信与代码互斥
* `message_send(project, to_agent, subject, content, ...)`：跨 Agent 异步信箱点对点发送（带收件箱容量防灌爆校验 `acl_mailbox_has_room`）
* `message_inbox(agent_name, unread_only, limit)`：异步信箱收件箱查阅与角标统计
* `message_action(action, message_id)`：邮件状态流转管理 (`read` 已读核销, `archive` 归档/解归档)
* `lease_manage(action, file_path, ...)`：代码文件并发软锁租约管理 (`acquire`, `release`, `renew`, `list`)，防止多 Agent 并发写冲突

### 4. 全局概览与正典导出
* `logbook_brief(project)`：一次调用返回全项目 open tasks/findings/waitings + running 批次 + 最近 devlog 标题的 brief digest，单包严格 ≤2K token
* `export_markdown(project, output_path)`：双向无损将事实数据导出为 100% 格式对齐的 DEVLOG.md（带路径白名单安全隔离）

### 5. 上下文 Resources (只读 URI)
* `logbook://{project}/tasks/active`：该项目当前活跃/进行中任务
* `logbook://{project}/waitings/open`：该项目当前待决/阻塞项
* `logbook://rules/engineering`：全团队工程铁律单一事实源

### 6. 规程 Prompts (模板指令)
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

### 1. 一键启动全栈服务 (Docker Compose)

只需一行命令即可在本地或云服务器启动轻量 PostgreSQL 18 底座与 Logbook App 服务：

```bash
docker compose up -d
```
> 实测全栈常驻物理内存仅 ~100MB（App ~40MB + DB ~60MB），针对 2C2G 边缘云及家庭 NAS 深度优化。
> 
> * 嵌入式 NAS（QNAP / 群晖）生产编排模板见 [`deploy/qnap/compose.yaml.example`](./deploy/qnap/compose.yaml.example)。

### 2. 配置 MCP 客户端 (以 Claude Desktop / Cursor / Antigravity 为例)

#### 方式 A：Stdio 本地直连 (开发测试首选)
在客户端配置文件（如 `claude_desktop_config.json` 或 `mcp_config.json`）中写入：
```json
{
  "mcpServers": {
    "logbook": {
      "command": "uv",
      "args": ["--directory", "/path/to/logbook", "run", "python", "-m", "logbook.cli", "mcp"],
      "env": {
        "DATABASE_URL": "postgresql://logbook:logbook_dev_secret@127.0.0.1:5432/logbook",
        "APP_ENV": "production"
      }
    }
  }
}
```

#### 方式 B：SSH 安全远程管道 (多 Agent 协同 / 生产首选)
跨主机、容器或多开发节点可通过 SSH 原生隧道安全接入，天然自带调用端 IP 溯源审计：
```json
{
  "mcpServers": {
    "logbook": {
      "command": "ssh",
      "args": ["-o", "BatchMode=yes", "sysadmin@<your-server-ip>", "mcp"]
    }
  }
}
```

#### 方式 C：FastMCP 原生 HTTP / SSE 监听
容器默认暴露 `8000` 端口提供标准 SSE 传输：
* SSE 端点：`http://<your-server-ip>:8000/sse`
* 探针端点：`http://<your-server-ip>:8000/health`

### 3. 向量嵌入模型配置 (三级自适应)

Logbook 原生支持 768 维语义向量，环境自适应优先级如下：
1. **OpenAI / Ollama / 本地兼容接口**：配置 `EMBEDDING_API_URL` 与 `EMBEDDING_API_KEY`；
2. **讯飞星火 MaaS 接口**：配置 `SPARK_API_KEY`（国内无代理极速直连）；
3. **零外部依赖离线降级**：若未配置外部 API Key，自动启用内置确定性 768 维哈希归一化降级，测试态与纯内网开箱即用。

---

## 📄 研发正典纪律

本项目严格执行 [`AGENTS.md`](./AGENTS.md) 与 [`docs/DEVLOG.md`](./docs/DEVLOG.md) 中规定的研发正典纪律。所有 Agent 开工前必须先行通读台账。

---

## 📜 许可证

[Apache-2.0 License](./LICENSE)
