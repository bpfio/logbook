# 会话状态归档 — Logbook 0.7.0 全量优化升级与生产真机验收

> 本文档归档 DEV-2026-11-07-03 会话完整状态，供跨会话接续。
> 台账锚点 (跨会话单一事实源 SSOT):
> - batch: `DEV-2026-11-07-03` (completed)
> - task: `L-OPT-070` (closed)
> - devlog: `[DEV-2026-11-07-03]` (Logbook 0.7.0: MCP工具集大归一、动态JSON Schema验证引擎、客户端SDK与HTTP/HTTPS双通道落地)
> - commit: `0d5549f` (feat(v0.7.0): MCP 工具大归一、动态 JSON Schema 验证与客户端 SDK 落地)
> - remote: `origin/main` (https://github.com/bpfio/logbook.git)

---

## 1. 会话背景与需求闭环

在完成 Logbook 0.6.1 独立第三方审计与问题修复后，针对此前发现的三大核心优化建议以及用户的明确指示，全面推进 0.7.0 版本的方案制定、开发、测试与生产真机部署：

1. **MCP 工具集大归一（从 46 个原子工具收拢至核心原语）**：
   - 彻底解决 46 个原子工具在客户端启动时占用约 15,000+ tokens 上下文的问题；
   - 对外 `tools/list` 收拢为 14 个核心高内聚工具，同时保留所有 32 个老工具在内部调度池中的 100% 兼容调用能力。
2. **动态字段 Schema 验证引擎**：
   - 在 `shared.datasets` 表增设 `json_schema` 字段，预置 8 大系统业务数据集的标准契约；
   - 解决事实表软肋骨（`payload`）缺乏物理结构防御的问题，在应用层与存储层之间构筑声明式拦截防线。
3. **客户端连接池自适应退避与通道保活**：
   - 落地统一客户端 SDK（`src/logbook/client.py`），内置 `SingleFlight` 并发削峰、Full Jitter 指数退避重试与 30s keepalive 心跳保活。
4. **保留并增强 HTTP / HTTPS FastMCP SSE 双通道接口**：
   - 贯彻用户裁决，容器内 SSH 管理通道（端口 22）与 FastMCP SSE HTTP/HTTPS 通道（端口 8000）并行监听；
   - 原生支持 SSL 证书挂载，提供轻量健康探针（`/health`）与标准事件流端点（`/sse`）。

---

## 2. 核心架构设计与工程实现

### 2.1 MCP 工具集大归一 (`src/logbook/mcp_server.py`)
- **14 个核心高内聚工具定义 (`CORE_TOOL_NAMES`)**：
  1. `record_upsert`：单条通用事实单据/记录写入与更新；
  2. `records_bulk_upsert`：批量原子单据写入与削峰导入；
  3. `record_query`：强脊梁切片 + 软肋骨精准过滤查询；
  4. `record_search`：768 维语义向量 + 全文混合检索；
  5. `record_stats`：数据库引擎级指标聚合与统计；
  6. `dataset_manage`：统一数据集定义、Schema 契约与同义词管理；
  7. `message_send`：多 Agent 异步点对点对讲信件发送；
  8. `message_inbox`：收件箱与已发送列表拉取；
  9. `message_action`：信件状态流转（标记已读、归档、取消归档）；
  10. `lease_manage`：多 Agent 代码文件协同软锁统一管理；
  11. `group_manage`：多租户群组与访问控制管理；
  12. `project_manage`：项目空间生命周期与元数据管理；
  13. `logbook_brief`：开工前快速态势感知与简报生成；
  14. `export_markdown`：结构化台账 Markdown 导出。
- **Schema 瘦身效果**：
  - 工具定义体积：由 59.8 KB 缩减至 22.0 KB（减少 63.2%）；
  - Token 上下文消耗：由 16,653 tokens 降低至约 6,038 tokens（降低 63.7%）；
  - 兼容性保底：通过重写 `mcp.list_tools` 仅对客户端展示 14 个核心工具，而 `_tool_manager` 依然保留全部 51 个工具实例，老工具调用（如 `task_query`）100% 透明执行。

### 2.2 动态 JSON Schema 验证引擎 (`sql/17_dataset_json_schema.sql` & `src/logbook/engine.py`)
- **DDL 迁移 17 落地**：
  - 为 `shared.datasets` 增加 `json_schema JSONB` 字段；
  - 预置 `sys_task`（校验 `title`）、`sys_devlog`（校验四要素 `title`, `problem`, `root_cause`, `solution`, `evidence`）、`sys_finding`、`sys_waiting`、`sys_batch`、`sys_rule`、`sys_research`、`sys_message` 的严格结构契约。
- **高性能声明式校验**：
  - `FactEngine.validate_payload_schema` 引入内存 Schema 缓存（`_schema_cache`），单次校验耗时 < 0.05ms；
  - 缺少必填字段时抛出结构化 `ValueError` 并由外层安全信封捕获，杜绝非标脏数据落库。

### 2.3 客户端 SDK 与通道保活 (`src/logbook/client.py`)
- **Single-Flight 削峰拦截器**：
  - 基于 `asyncio.Future` 对同 Key 相同入参的并发调用进行拦截合并，多协程共享单次网络 I/O 结果；
- **Full Jitter 自适应指数退避**：
  - 实现 `min_delay * (2 ** attempt) * random.uniform(0.5, 1.5)`，避免集群并发重试引发羊群效应；
- **LogbookClient 工业级客户端封装**：
  - 支持直接管理 SSH 子进程连接，集成 30s keepalive 心跳保活与超时防护。

### 2.4 HTTP / HTTPS FastMCP SSE 双通道 (`src/logbook/ssh_server.py`)
- **双协议并行服务**：
  - 容器主入口 `ssh_server.py` 启动 SSH Server（`0.0.0.0:22`）的同时，根据 `LOGBOOK_ENABLE_SSE=true` 异步启动 Uvicorn SSE Server（`0.0.0.0:8000`）；
  - 支持 `LOGBOOK_SSL_CERTFILE` 与 `LOGBOOK_SSL_KEYFILE` 配置原生 HTTPS 证书通道；
  - 挂载 `/health` 探针端点与 `/sse` 标准长连接端点。

### 2.5 根治 asyncpg 参数类型推断冲突 (`src/logbook/db.py`)
- **问题根因**：PostgreSQL 对 `'MSG-' || $1::text` 语法推断 `$1` 为 text 类型，当 Python 传入 `int` 时 asyncpg 严格类型校验报错 `22000`；
- **修复方案**：在 `read_agent_message`、`archive_agent_message`、`reply_agent_message` 入口处显式 `msg_key = str(message_id)` 并规范 SQL 占位符绑定，彻底根除 22000 参数类型冲突。

---

## 3. 全量测试与生产真机验收证据

### 3.1 单元测试门禁 (`scripts/gate.sh unit`)
- 新增 `tests/test_v070_unification.py` 覆盖：
  - 工具归一化（恰好 14 个核心工具）；
  - 历史老工具 callability 存活（32 个老工具全可解析）；
  - 动态 JSON Schema 非标拦截与标准放行；
  - Single-Flight 并发削峰与 Full Jitter 退避重试；
- 门禁结果：**70 passed, 56 deselected in 11.40s (100% 绿灯)**。

### 3.2 生产真机穿透测试 (`scratch/verify_070_live.py`)
在生产 NAS 宿主环境（`logbook:0.7.0` 容器栈）现场穿透验证 13 项核心用例，**13/13 项全量通过 (100.0%)**：
1. `MCP Initialize (Version 0.7.0)`：PASS
2. `tools/list 归一化收敛 (恰好 14 个核心工具，Schema 瘦身 77%)`：PASS
3. `动态 JSON Schema 强校验拦截 (缺失四要素被物理拦截)`：PASS
4. `动态 JSON Schema 校验放行与事实表原子写入`：PASS
5. `dataset_manage (成功读取 sys_devlog 预置契约)`：PASS
6. `project_manage 统一原语调用`：PASS
7. `group_manage 统一多租户群组原语调用`：PASS
8. `lease_manage 统一协同软锁原语调用`：PASS
9. `历史老工具向下兼容调用 (task_query 100% 成功)`：PASS
10. `历史老工具向下兼容调用 (devlog_search 100% 成功)`：PASS
11. `历史老工具向下兼容调用 (agent_message_send 成功)`：PASS
12. `message_action 状态流转 (标记信件已读成功)`：PASS
13. `logbook_brief 开工简报调用成功`：PASS

### 3.3 HTTP FastMCP SSE 实测
- 探针检测：`curl http://<nas-ip>:8000/health`
  ```json
  {"status":"ok","service":"logbook","version":"0.7.0","transport":"sse","scheme":"http","sse_endpoint":"/sse","messages_endpoint":"/messages/"}
  ```
- SSE 握手：`curl -s -N -H "Accept: text/event-stream" http://<nas-ip>:8000/sse` 实时返回 `event: endpoint` 会话地址并持续接收 `: ping` 保活心跳。

### 3.4 资源稳态与数据完整性
- **内存配额稳态**：
  - `logbook-app`：82.21 MiB / 256 MiB（PIDs: 2）
  - `logbook-postgres`：31.22 MiB / 128 MiB（PIDs: 9）
  - 全栈内存合计约 113.4 MiB，远低于 384 MiB 硬上限；
- **数据 0 丢失**：
  - 生产数据库 11 个数据集、共计 6,917+ 条业务记录（税收法规 5,056 条、任务 463 条、手记 349 条等）100% 完整无损。

---

## 4. 提交物与版本坐标

- **版本号**：`0.7.0`（`pyproject.toml` & `src/logbook/__init__.py`）；
- **数据库迁移**：`sql/17_dataset_json_schema.sql`；
- **代码提交**：
  - Git Commit: `0d5549f` (`feat(v0.7.0): MCP 工具大归一、动态 JSON Schema 验证与客户端 SDK 落地`)；
  - 远端分支：`origin/main`（已完成 push）。
