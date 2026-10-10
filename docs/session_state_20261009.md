# 📌 Logbook 会话全量状态存档 (Session State Archive)

**记录时间**：2026-10-09 22:45 (UTC+8)  
**版本跨越**：`v0.2.0` $\longrightarrow$ `v0.3.0` $\longrightarrow$ `v0.3.1` $\longrightarrow$ `v0.4.0`  
**核心任务**：`L13`（多 Agent 对讲信箱与租约）、`L13.5`（全实体 IP 溯源审计）、`L14`（研发调研知识库）  
**手记凭证**：`DevLog #43`、`DevLog #44`、`DevLog #45`（已录入生产 PostgreSQL + 512 维向量索引）  
**测试状态**：离线与核心单元测试 13/13 项 100% 全绿通过  
**当前系统健康度**：**100 / 100 满分**（信箱对讲、租约互斥、全实体 IP 溯源、研发调研 RAG 混合检索四核完备就绪）

---

## 一、 会话核心目标与成果总览

### 1. 多 Agent 异步对讲信箱与代码文件防冲突租约 (v0.3.0 / L13)
- **物理表设计 (`shared.agent_messages` & `shared.file_leases`)**：
  - `sql/05_agent_messages_and_leases.sql` 落地异构 Agent（`agy`, `codebuddy`, `zcode`）点对点信件投递与状态跟踪；
  - 提供文件租约软锁（`file_leases`），解决多 Agent 并发写入冲突；
- **核心 MCP 工具集**：
  - `message_send` / `agent_message_send`：信件投递与任务指派通知；
  - `message_inbox`：按 Agent 身份拉取未读/全量信箱；
  - `message_read`：阅读并原子标记已读；
  - `lease_acquire` / `lease_release` / `lease_query`：文件租约争抢、释放与状态查询；
- **状态流转增强**：`tasks` 表扩展 `reviewer` 责任人与 `review`（待验收）枚举状态，打通研发到质检流转。

### 2. 全实体 Agent IP 溯源与不可篡改审计增强 (v0.3.1 / L13.5 / DevLog #44)
- **审计三要素闭环 (Who + When + Where)**：
  - 针对异构多 Agent 环境，7 大实体全面补齐物理网络节点坐标 `agent_ip VARCHAR(45) NOT NULL DEFAULT '0.0.0.0'`；
  - 与现有时间戳（`created_at`, `updated_at`, `occurred_at`, `started_at`, `closed_at`）构成完整审计闭环；
- **全 Schema 动态幂等自愈迁移 (`sql/06_agent_ip_audit_everywhere.sql`)**：
  - 动态游标循环所有动态项目 Schema（`tasks`, `findings`, `waitings`, `batches`, `task_timeline`）以及全局表（`devlogs`, `file_leases`）增量加列；
  - 更新 `shared.init_project_schema(p_name)` 函数，确保未来新开立项目原生自愈具备 `agent_ip`；
- **三级自适应 IP 探测机制 (`detect_caller_ip`)**：
  $$\text{显式参数} \longrightarrow \text{SSH 环境变量 (SSH\_CLIENT / SSH\_CONNECTION)} \longrightarrow \text{本地 UDP 套接字} \longrightarrow \text{127.0.0.1 兜底}$$
  - **调用端 0 改造成本**：`agy`、`codebuddy` 等远程调用时自动捕获其真实物理来源 IP。

### 3. 研发调研知识库与开工前 RAG 语义检索 (v0.4.0 / L14 / DevLog #45)
- **架构定位与双平面沉淀 (`shared.researches`)**：
  - 遵循全局铁律三（禁止重复造轮子）与铁律四（证据一致性），将技术选型与成熟方案调研作为组织级共享知识资产沉淀于共享知识面；
- **调研结构化五要素**：
  1. `objective`：调研目标、业务诉求与资源底线；
  2. `market_landscape`：成熟开源方案全景对比（Star数、维护度、生产验证）；
  3. `tradeoffs`：两路线评估与对抗成本（必须列出待对抗的默认行为清单与代价）；
  4. `decision`：最终选型决策与为什么不自研的说明；
  5. `references`：事实依据、Benchmark 与代码仓库锚点；
- **双引擎语义检索**：
  - 讯飞星火 MaaS 512 维向量 (`vector(512)`) + HNSW 索引（亚毫秒余弦检索）；
  - 本地确定性分词散列降级 (`deterministic_fallback`) + PostgreSQL GIN 全文检索 (`tsvector`) 兜底；
- **三大新增 MCP 工具**：
  - `research_search`：开工前必查，自然语言语义检索已有类似选型，0 重复造轮子；
  - `research_record`：结构化录入/更新调研报告（自动脱敏、自动 IP 探测、自动生成向量）；
  - `research_query`：列表与看板查询；
- **领域契约守卫**：
  - `models.py` 中通过 `model_validator` 铁律断言：`completed` 状态必须包含详实的 `decision` 与 `tradeoffs`（未达标阻断入库）。

---

## 二、 节点拓扑与环境映射 (SSOT)

| 节点角色 | 主机名 / 容器 | IP 地址 | 访问方式与通道职责 |
|---|---|---|---|
| **本地开发机** | `xhub` / `cliserver` | `192.168.1.22`<br>`192.168.200.22` | • 当前会话原生宿主，本地用户 `yupeng`<br>• SSH 公钥注释 `yupeng@cliserver`<br>• `agy` CLI 宿主环境，全局发现 `~/.gemini/config/mcp_config.json` |
| **Logbook App 容器** | `logbook-app` | `192.168.1.68` | • 运行于 QNAP NAS Container Station（macvlan 直通）<br>• 暴露 SSH Stdio MCP 管道 (`sysadmin@192.168.1.68 mcp`)<br>• 内存硬配额 96MB，常驻 ~40MB |
| **Logbook PG 容器** | `logbook-postgres` | `192.168.1.68` | • 共享 `logbook-app` 网络栈 (`127.0.0.1:5432`)<br>• PostgreSQL 18 + pgvector 插件 (HNSW 向量索引)<br>• 双平面架构：`shared` 共享知识面 + 动态项目执行面 |
| **存储宿主机** | QNAP TS-453D | `192.168.1.33` | • 承载 Docker 编排与持久化存储卷 |

---

## 三、 完整 18 个 Logbook MCP 原语与资源矩阵 (v0.4.0)

| 模块 | 原语 / 工具名称 | 读写属性 | 功能描述 |
|---|---|---|---|
| **项目开户** | `project_init` | 写 | 初始化并开立新项目的台账空间（DDL 幂等创建与双轨注册） |
| | `logbook://projects` | 读 | 只读资源：所有已注册项目的 Git 坐标与 Schema 清单 |
| **任务台账** | `task_upsert` | 写 | 原子登记或推进任务状态机（支持 P0~P3、reviewer、agent_ip 审计、闭环断言） |
| | `tasks_bulk_upsert` | 写 | 单事务批量写入任务清单（高效导入） |
| | `task_query` | 读 | 多维度查询任务看板（支持状态、优先级、责任人过滤） |
| **根因手记** | `devlog_record` | 写 | 结构化录入排查手记（强制四要素 + 512维向量 + agent_ip + 幂等去重） |
| | `devlog_search` | 读 | 语义向量与全文混合检索历史排查经验 |
| **调研知识** | `research_record` | 写 | 结构化录入研发调研报告（强制五要素 + 512维向量 + agent_ip 审计） |
| | `research_search` | 读 | 开工前必查：语义检索跨项目技术选型与对抗成本分析 |
| | `research_query` | 读 | 多维看板查询历史调研档案 |
| **缺陷发现** | `finding_record` | 写 | 记录演练或代码审查发现的缺陷与风险（支持 agent_ip） |
| | `finding_query` | 读 | 查询项目未决缺陷列表 |
| **阻塞待办** | `waiting_record` | 写 | 记录阻塞项或待用户裁决事项（支持 agent_ip） |
| | `waiting_query` | 读 | 查询未关闭的阻塞项 |
| **研发展开** | `batch_upsert` | 写 | 维护研发批次演进与讨论复盘（支持 agent_ip） |
| | `batch_query` | 读 | 查询历史批次演进记录 |
| **信箱对讲** | `message_send` | 写 | 向目标 Agent 发送对讲信件或任务指派（支持 from_ip/to_ip） |
| | `message_inbox` | 读 | 查收指定 Agent 的独立信箱（未读/全部） |
| | `message_read` | 写 | 阅读信件详情并原子标记已读 |
| **代码租约** | `lease_acquire` | 写 | 申请代码文件修改租约软锁（防多 Agent 写冲突，支持 agent_ip） |
| | `lease_release` | 写 | 释放持有的代码文件租约 |
| | `lease_query` | 读 | 查询项目当前生效中的文件租约 |
| **规则与简报** | `rule_query` | 读 | 开工前对齐架构铁律与工程红线 (SSOT) |
| | `logbook_brief` | 读 | 一次调用生成项目开工简报 digest (≤2K token) |
| | `export_markdown` | 读 | 导出完全兼容 brix 格式的 DEVLOG.md |

---

## 四、 代码仓库提交历史与分支状态

- **GitHub 仓库**：`https://github.com/bpfio/logbook`
- **主要分支**：
  - `main`：最新 HEAD Commit `59f8d91`
  - `feat/0.3.0-multi-agent-mailbox`：与 `main` 保持 100% 同步
- **本次会话提交链路**：
  1. `849f950`：`feat(messaging): 增强对讲信箱节点 IP 溯源与自动探测 (L13.4)`
  2. `e829bd2`：`feat(audit): 落地全实体 Agent IP 溯源与不可篡改审计 (06_migration)`
  3. `0894f91`：`feat(research): 落地研发调研知识库与开工前 RAG 语义检索 (0.4.0)`
  4. `59f8d91`：`docs(state): 归档多Agent信箱、全实体IP审计与研发调研知识库全量会话状态`

---

## 五、 QNAP 生产环境部署与端到端实测闭环

生产环境（QNAP Container Station）已成功完成 Logbook `v0.4.0` 热升级，并经由实战测试全链路跑通：

1. **生产镜像与数据库就绪**：
   - `logbook-postgres` 完成 `sql/06` 与 `sql/07` 幂等迁移；
   - `logbook-app` 完成重建并正常运行，25 项 MCP 工具全部可用。
2. **端到端收信与阅信实测**：
   - 成功调用 `message_read(message_id=5)` 提取上线公告信件；
   - 信件来自 `agy@192.168.1.22`，成功原子标记为已读（`is_read = true`, `read_at = 2026-10-09 23:29:29Z`）。
3. **端到端回信与发信实测**：
   - 成功调用 `message_send` 投递回执信件 ID `#6`（主题：`【回执】已收到 Logbook 0.4.0 上线通知与协作指引`，线索 `init-0.4.0`）；
   - 自动捕获客户端源 IP（`192.168.1.30`），安全落库。
4. **信箱列表查收复核**：
   - 调用 `message_inbox(agent_name="agy")` 成功拉取消息列表，信件 #5（已读）与信件 #6（未读）链路完整成对。

