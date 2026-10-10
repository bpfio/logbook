# LOGBOOK 开发日志 (DEVLOG) — 项目唯一开发日志与任务台账

> **规则 (AGENTS.md SSOT)**:
> ① 每次开发开工前必须先行通读本文件；
> ② 每个任务一条记录，动态维护；
> ③ 本文件随变更提交项目仓库，是任务状态唯一正典；
> ④ 状态集: 🔵 planned / 🟢 running / ✅ closed / ⏸ blocked / 📋 wontfix。
> ⑤ 排序: 最新在前。

---

## 一、任务台账 (19)

| ID | 状态 | 类型 | 标题 | commit | 备注 |
|---|---|---|---|---|---|
| L-MSG-1 | closed | fix | 对讲邮件子系统生产故障修复: message_* 调用即断连 (L-MSG-1 办结) | 3259ffe | 根因消除/单进程DDL首检缓存/asyncio.shield守护/SSH管道300ms缓冲/个人信箱资源上线 |
| L14 | closed | feat | 研发调研知识库 (shared.researches) 与开工前 RAG 语义检索落地 | 0894f91 | sql/07_research_knowledge_base.sql + research_record/search/query 三大MCP工具 + 13测试全绿 |
| L13.5 | closed | feat | 全实体Agent IP溯源与不可篡改审计增强 (06_migration) | e829bd2 | sql/06_agent_ip_audit_everywhere.sql + detect_caller_ip三级探测 + 9测试全绿 |
| L13.4 | closed | deploy | QNAP生产环境镜像构建、平滑热升级与多Agent端到端协同验证 | 87d5b9e | DevLog #43 / 生产镜像上线(67.8MB), 25工具+8资源+4规程真机验证PASS |
| L13.3 | closed | feat | FastMCP工具注册与端到端测试套件扩充 (mcp_server.py & tests/) | d8fc1c5 | 暴露信箱与租约6大MCP工具，task_upsert增加reviewer参数 |
| L13.2 | closed | feat | 数据模型层与数据库核心CRUD操作实现 (src/logbook/models.py & db.py) | d8fc1c5 | 落地AgentMessage/FileLease校验模型、状态归一化与db异步信箱租约原子操作 |
| L13.1 | closed | feat | 多Agent对讲信箱与代码文件租约软锁核心表DDL建模与平滑迁移 | d8fc1c5 | sql/05_agent_messages_and_leases.sql + tasks表支持reviewer与review状态 |
| L13 | closed | feat | 支持多 Agent 异步对讲信箱、代码文件防冲突租约与协同流转 | d8fc1c5 | 0.3.0 里程碑主任务闭环 |
| L12 | closed | feat | Git仓库坐标权威锚定、双轨项目协商与自愈开户原语落地 | 0448a89 | sql/04_project_registry.sql + project_init MCP原语 + 双轨寻址41测试全绿 |
| L11 | closed | ops | cliserver开发机识别与agy生产logbook MCP配置核验归档 | - | 确证cliserver即xhub; 全局mcp_config.json已生效; logbook_brief RPC验证PASS |
| L10 | closed | deploy | 优化升级全量落地、03_dedup迁移与QNAP生产环境0.2.0发布 | HEAD | 36测试全绿, 18笔重放通过, 生产内存71MB |
| L08 | closed | ops | Brix全量核验、讯飞768维MRL评测与zcode生产MCP接入落地 |  | ~/.zcode/cli/setting.json |
| L07 | closed | feat | 6大工程优化全量落地(统一返回包装/批量原语/向量缓存去重/参数归一化防呆) | f3a6599 | scripts/mcp_ingest_brix.py |
| L05.4 | closed | deploy | QNAP 生产容器内存配额再平衡与全维度基准联测 | d27ac5f | deploy/qnap/compose.yaml |
| L05.3 | closed | fix | SSH MCP 孤儿子进程泄漏与内存膨胀根因定位与修复 | d27ac5f | src/logbook/ssh_server.py |
| L05.2 | closed | feat | 代码模型层与 MCP 工具全量对齐及测试套件扩充 | 04eab61 | tests/test_logbook.py |
| L05.1 | closed | feat | 字段扩展 DDL 建模与平滑迁移脚本编写执行 | 04eab61 | sql/02_optimize_fields.sql |
| L05 | closed | feat | 数据表字段深度优化、多Agent协同扩容与全量生产联调联测 | d27ac5f | tests/benchmark_live.py |
| L04 | closed | feat | QNAP 生产环境无缝上线、Rekall 彻底下线与星火向量生产验证 | 0477f75 | deploy/qnap/compose.yaml |
| L03 | closed | feat | 强制项目显式传参、三级交互协商自愈与全维 MCP 2.x 升级 | 5031c53 | tests/test_negotiation.py |
| L02 | closed | feat | 双平面隔离底座、PostgreSQL 18+pgvector、FastMCP 与 CLI 看板落地 | c51bbd7 | tests/test_logbook.py |
| L01 | closed | feat | Logbook 创世纪立项、规则制定与基础脚手架建立 | HEAD | README.md |

---

## 二、发现台账 (6)

| ID | 来源 | 级别 | 状态 | 处置 | 备注 |
|---|---|---|---|---|---|
| F-MSG-01 | zcode | P1 | fixed | 消除重复 DDL、写事务 shield 保护、管道流缓冲，彻底根治 -32000 Connection closed | message_* 每次调用执行 10 条 DDL 导致高时延，非交互式管道 EOF 触发 MCP AnyIO cancel_scope 取消在途任务 |
| F-LOG-05 | simulation | P2 | fixed | 解耦非代码任务证据锚点，自动将实测 notes 锚定为合规 proof_link | 非代码任务（演练/调查/运维）强绑定 commit_hash 导致合规闭环失真或伪造占位符 |
| F-LOG-04 | simulation | P2 | fixed | 引入 normalizer.py 并在 FastMCP 中通过 Pydantic BeforeValidator 实现前置同义词与Emoji归一化 | Pydantic Literal 强拦截导致带 Emoji 或同义词时抛 400 校验异常中断执行流 |
| F-LOG-03 | simulation | P2 | fixed | 基于四要素文本对比实现幂等更新与向量缓存复用 (标记 cached_skip 零API开销) | DevLog 重复录入未做内容查重导致向量 API 配额浪费与检索重复召回 |
| F-LOG-02 | simulation | P2 | fixed | 新增 tasks_bulk_upsert 原语并在单事务中批量提交 (耗时压制至 98ms) | 缺乏原子批量写入原语导致批量导入产生数十次高延迟网络往返 RTT |
| F-LOG-01 | simulation | P2 | fixed | 统一使用结构化包装字典包裹返回列表并支持分页元数据 | FastMCP 列表返回值序列化碎片化导致第三方客户端截断读取 (仅读首块丢失后续数据) |

---

## 三、待办/待用户 (0)

| ID | 类别 | 状态 | 事项 |
|---|---|---|---|

---

## [DEV-2026-10-10-01] 对讲信箱引擎生产故障根因定位、DDL首检缓存与管道生命周期加固 — ✅ 闭环

完成对讲邮件子系统生产故障 (L-MSG-1) 根因定位、全链路加固与 QNAP TS-453D 生产平滑热投产：
1. **故障根因定位与排查**:
   - 确证非权限不足或物理表缺失。根因为 db.py 每次调用 message_* 均重复执行 10 条 DDL 语句导致单次耗时达数百毫秒并持排他锁；
   - 非交互式管道客户端发送完请求即到达 stdin EOF，触发 MCP AnyIO 引擎执行 `tg.cancel_scope.cancel()` 强行取消在途 task，引发 `asyncpg` 连接异常中断并向客户端返回 `-32000 Connection closed`。
2. **核心加固与业务功能升级**:
   - **DDL 缓存自愈**: 在 Database 引入 `_messaging_schema_ensured` 与 `_researches_schema_ensured` 看门狗标志，仅在进程启动首检时执行一次 DDL，后续调用 0ms 直通，信箱读写耗时从 500ms 降至 2ms；
   - **任务盾牌保护**: `message_send` 底层通过 `asyncio.shield()` 保护数据库写事务，防止调用端提前断连导致写入中断；
   - **SSH 管道流缓冲**: `ssh_server.pipe_streams` 在 EOF 退出点增加 300ms 优雅缓冲，确保非交互式管道调用的响应完整写回客户端；
   - **MCP Resource 原生暴露**: 新增 `logbook://mailbox/{agent_name}/inbox` 与 `logbook://{project}/mailbox/{agent_name}` 个人信箱只读挂载资源；
   - **开工简报集成**: `logbook_brief` 首行实时输出 `mailbox_unread` 统计与未读信件索引；
   - **物理真实 IP 溯源**: 全链路保留 `192.168.1.22`、`192.168.1.30`、`192.168.1.68` 物理局域网真实 IP，杜绝保留段占位符。
3. **单元测试与门禁验证**:
   - 扩充 `tests/test_messaging_and_leases.py`，全量 7 项信箱与租约测试 100% PASS (3.31s)；
4. **QNAP 生产平滑热部署与真机对讲对账**:
   - 构建 `logbook:0.4.0` 镜像并推流至 QNAP TS-453D (`192.168.1.33`)，Docker Compose 热重载；
   - 提取到此前 zcode 从 `192.168.1.22` 投递至 agy 的信件 **#7**；
   - 成功向 zcode 投递加固闭环通知信件 **#8**（主题：`【闭环通知】对讲信箱引擎已加固修复 (L-MSG-1 办结)`）；
   - 挂载 `logbook://mailbox/zcode/inbox` 资源验证，信件 **#8** 实时渲染且完整包含发件端真实 IP；
   - 验证 `logbook_brief` 实时呈现 `mailbox_unread=2`（含信件 #7 与 #8）。
5. **台账与手记闭环**:
   - 任务台账 `task L-MSG-1` 状态已在生产库更新为 **`closed`** (挂接提交 `3259ffe` 与手记指针)；
   - 缺陷管理 `finding L-MSG-1` 状态已在生产库更新为 **`fixed`**；
   - 生产数据库向量库录入排查手记 **`devlog[50]`** (已生成 512 维向量索引)。

---

## [DEV-2026-10-09-03] Logbook 0.4.0 全维 MCP 协议吸收与 QNAP 生产环境无缝投产 — ✅ 闭环

完成 Logbook 0.4.0 全维 MCP 协议吸收、SQL 迁移死角排障与 QNAP TS-453D 生产平滑热投产：
1. **架构与业务审计**: 深度审查 0.3.0 (对讲信箱/租约软锁)、0.3.1 (全实体 IP 溯源审计) 与 0.4.0 (调研知识库)，修复 SQL 保留字 references 未转义语法隐患与 pg_toast 系统 Schema 遍历越权隐患 (提交 f8524ae)；
2. **全维 MCP 协议吸收**: 落地 3 大 Resources 只读挂载端点 (leases/active, researches/recent, mailbox/summary) 与 2 大 Prompts 规程模板 (conduct_research, agent_collaborate)，信箱发信固有回显全局数字自增 ID，看板直出待提取 ID 索引引导收件 Agent 原子核销 (提交 87d5b9e)；
3. **全覆盖单元测试**: 落地 tests/test_resources_and_prompts.py，全套 20 项测试 100% 全绿 (3.26s)；
4. **QNAP 生产五步平滑投产**:
   - 生产 PG 数据库冷备先行 (logbook_pre_20261009_234403.dump, 167KB)；
   - 极简生产镜像 logbook:0.4.0 (84.9MB) 17 秒流式导入 QNAP Docker；
   - 物理迁移 05、06、07 脚本 100% 成功执行；
   - Docker Compose 热重载，全栈常驻内存仅 67.8MB (App 42.8MB, PG 24.9MB，CPU 0.00% / 0.03%)；
   - SSH MCP 真机实测通过 25 个工具全集、8 个只读资源与 4 个工作流模板，端到端信件与租约软锁流体验证通过；
5. **台账与手记闭环**: 研发任务 L13.4 正式办结归档，生产向量库录入 DevLog #43 (讯飞星火 512 维向量索引就绪)。

---

## [DEV-2026-10-09-02] 研发调研知识库 (shared.researches) 与开工前 RAG 语义检索 — ✅ 闭环

完成研发调研知识库全链路研发、测试与发布 (Logbook v0.4.0):
1. **共享知识面物理 DDL**: `sql/07_research_knowledge_base.sql` 建立 `shared.researches`，强制约束调研五要素 (objective, market_landscape, tradeoffs, decision, references)，配置 HNSW 向量索引 (512维) 与 GIN 倒排索引；
2. **强类型模型与铁律断言**: `models.py` 与 `normalizer.py` 增加 `Research` 实体模型与分类/状态归一化，强制断言 `completed` 状态必须包含详实决策与对抗成本评估；
3. **双引擎混合检索与数据库操作**: `db.py` 实现 `record_research`, `update_research`, `search_researches`（语义向量+全文混合召回）与 `query_researches`；
4. **三大 MCP 原语上线**: `research_record`, `research_search`, `research_query` 支持 0 摩擦调用并集成三级 IP 溯源；
5. **单元测试与台账归档**: `tests/test_researches.py` 覆盖 13 项测试 100% 全绿，台账任务 `L14` 办结，`DevLog #45` 归档。

---

## [DEV-2026-10-09-01] 多Agent对讲信箱、文件租约互斥与全实体IP溯源审计 — ✅ 闭环

完成对讲信箱、代码文件防冲突租约软锁与全实体 Agent IP 溯源审计 (Logbook v0.3.0 ~ v0.3.1):
1. **多 Agent 对讲信箱**: `sql/05_agent_messages_and_leases.sql` 建立 `shared.agent_messages`，支持点对点信件投递、信箱拉取、标记已读与任务状态推进 (`reviewer` / `review`)；
2. **代码文件租约软锁**: `shared.file_leases` 建立文件级 TTL 软锁，避免多 Agent 并发写入产生文件冲突；
3. **全实体 IP 溯源与不可篡改审计**: `sql/06_agent_ip_audit_everywhere.sql` 动态循环为所有 Schema 的 7 大实体补齐 `agent_ip VARCHAR(45)` 与索引，更新 `shared.init_project_schema`；
4. **三级自适应 IP 探测**: `detect_caller_ip` 自动提取 SSH 客户端物理源 IP，调用端 0 改造成本；
5. **单元测试与台账归档**: `tests/test_messaging_and_leases.py` 与 `tests/test_audit_ip_everywhere.py` 全绿通过，任务 `L13`、`L13.5` 办结，`DevLog #43`、`#44` 归档。

---

完成 Git 仓库坐标权威锚定、多项目双轨协商与 Agent 原生自愈开户原语落地与 QNAP 生产发布：
1. **权威注册中心 (SSOT) 与双轨解析**: `sql/04_project_registry.sql` 建立 `shared.projects` 登记中心，支持权威 Git 坐标 (`bpfio/brix`, `bpfio/logbook`, `io/TS`) 与物理 Schema (`brix`, `logbook`, `ts`) 双轨互通解析；
2. **Agent 原生自开户原语 (`project_init`)**: 新增 `project_init(repo, title, description)` MCP 工具，为 Agent 提供自开立与幂等建库能力，物理 Schema 自动规范化派生；
3. **两态智能协商自愈**: 优化 `negotiation.py`，智能区分高相似度手滑纠偏 (`CORRECT_PROJECT_PARAMETER`) 与新项目开户指引 (`PROJECT_INIT_NEEDED`)；
4. **工作区守卫与装饰器架构修复**: 修复无 Git 目录（如容器内 `/app`）工作区误锁定问题；将 FastMCP 工具装饰器顺序调整为 `@mcp.tool()` 在外、`@tool_shell` 在内，实现 100% 结构化错误兜底；
5. **生产验证与回归**: 全量测试套件 41/41 全绿通过；QNAP 生产容器完成热重建与热重载；现场通过 SSH MCP 验证 `project_init(repo="io/TS")` 与 `task_upsert` / `task_query` 闭环；
6. **跨目录写入规则与防护机制**: 确立跨项目目录写入三场景规则：① 非 Git 仓库目录（如 home/临时目录）：未绑定特定工作区，显式指定 `project` 即可直接写入；② 处于其他 Git 仓库目录：默认物理防呆拦截，显式传入 `allow_cross_project=True` 或环境变量 `LOGBOOK_ALLOW_CROSS_PROJECT=1` 放行；③ 远程 SSH MCP 模式：服务端沙盒隔离，原生支持按需向任意合法项目跨目录写入。

---

## [DEV-2026-10-08-11] cliserver 本地开发机识别与 agy 全局 logbook MCP 配置核验归档 — ✅ 闭环

完成 cliserver 主机身份确证与 agy 全局 logbook MCP 连通性生产核验：
1. **主机身份确证**: 当前运行宿主机 `xhub`（`192.168.1.22` / `192.168.200.22`）即网络正典中定义的本地开发机 `cliserver`（`~/.ssh/id_ed25519.pub` 注释为 `yupeng@cliserver`，`openwrt/AGENTS.md` 明确映射 `xhub / cliserver`），当前 Agent 已原生处于该环境，本地免密互信与 sshd 均正常；
2. **全局 MCP 配置与就绪**: `agy` 通过 `~/.gemini/config/mcp_config.json` 全局挂载 `logbook` SSH stdio 管道（`sysadmin@192.168.1.68 mcp`），状态为 `enabled`，14 个正典 MCP 原语就绪；
3. **端到端生产 RPC 验证**: 现场调用 `logbook_brief(project='logbook')`，耗时 15ms 成功返回生产数据库简报与历史手记，证实通道 100% 可用；
4. **状态与手记闭环**: 成功通过 `batch_upsert`、`task_upsert` (L11) 与 `devlog_record` (#27) 沉淀至生产 pgvector 向量库并完成会话存档。

---

## [DEV-2026-10-08-10] Logbook 0.2.0 六病一根优化全量落地与 QNAP 生产发布验证 — ✅ 闭环

五线 (A1 数据层/A2 工具壳错误面/A3 安全包/A4 Harness/A5 文档) 全量落地并完成 QNAP 生产平滑发布：
1. **数据层与去重收口**: `sql/03_dedup.sql` 成功清洗 12 条存量副本，创建 `uq_devlogs_proj_task_title` 唯一表达式索引，实现写路径统一与幂等；状态机首戳保护 (`resolved_at`)，消除 `closed_at` 覆盖漂移；
2. **错误面与提要端点**: 统一收敛为 `{isError: true, error_type, detail}`；前置拦截 `TASK_NOT_FOUND`；新增 `logbook_brief` 提要端点 (Token 仅消耗 224~825，省 93%)；写响应默认精简瘦身；读支持 `fields` 投影与 `limit <= 200` 强钳制；
3. **部署与安全隔离**: 凭据 100% 迁出 `.env`；宿主机备份先行 (`logbook_pre_20261008_041759.dump`, 96KB)；镜像流式导入平滑拉起；
4. **端到端真机回归**: 生产 SSH MCP 通道重放 18 笔真实历史写入 (23 笔判定 100% PASS，0 副本膨胀)；`logbook_brief` 与 `devlog_search` 生产实测通过；全栈常驻内存 71.4MB，Gitea 等现有服务零冲击。

---

## [DEV-2026-10-08-09] Logbook 优化设计正典开账 (PLAN_OPT_2026-10-08) — ✅ 闭环

基于 Brix 夜11/夜12 真实调用 25+ 次体感与深探报告，确立“二线经验检索库”定位与六病一根总图，制定 P0 正确性五件、P1 效率五件、P2 安全五件与三波实施验证门规程。

---

## [DEV-2026-10-07-08] Brix数据对账、向量原生维度测评与本地 zcode 生产 MCP 接入 — ✅ 闭环

完成 Brix 原文全量交叉核验 (4批次/20任务/91发现/14待办/4手记 100% 一致)；完成讯飞 768 维原生与 512 维 MRL 截断实测评估；完成本地 zcode 生产 SSH MCP 管道接入与 Doctor 验收。
验证方法论: 1. Brix Markdown与数据库双向全量交叉对账；2. 向量维度正负样本余弦相似度与区分边界对照测试；3. zcode doctor 官方诊断与 SSH JSON-RPC 2.0 握手实测。

---

## [DEV-2026-10-07-07] 6大工程优化全量落地与 MCP 端到端闭环验证 — ✅ 闭环

6大工程优化全量落地与 MCP 端到端闭环验证 (统一返回包装/批量原语/向量缓存去重/参数归一化防呆)
端到端模拟测试驱动排查与优化，Pydantic BeforeValidator 结合 Schema 自解释规范，PostgreSQL 单事务批量落库与向量哈希缓存

---

## [DEV-2026-10-07-05] Logbook 字段深度优化、多Agent协同与生产联调验证 — ✅ 闭环

针对数据表充分性进行系统性调研与多轮技术推演，完成任务分级 parent_id、协同锁 assignee、领域分类 category 等关键字段向后兼容 DDL 迁移；深入排查定位 AsyncSSH 子进程未写 EOF 导致的孤儿泄漏，彻底根治并再平衡容器内存配额 (app:96M/pg:48M)，实测常驻内存 46.4MB。全量 15 项测试与 8 大生产指标全绿。
1. 数据模型与 Multi-Agent 协作场景深度结合；2. 异步流管道必须在 EOF 时显式向下游写入 EOF，并在异常断连时强制回收子进程；3. 宿主机避免配置冗余 MCP 连接隧道以压制常驻内存。

---

## [DEV-2026-10-07-04] Logbook QNAP 生产环境部署与全栈替换上线 — ✅ 闭环

路线 A 彻底替换升级为 Logbook，回收生产节点 IP，配置讯飞星火向量，安全下线 Rekall 镜像，绝对保障 QNAP 上 Gitea 等既有服务 0 冲击。实测双容器仅占 ~63MB 物理内存。
1. Docker build 网络隔离避免 apt-get 依赖，纯 Python AsyncSSH 秒级构建；2. 容器共享网络模式必须联动启停；3. 讯飞星火 MaaS API 物理维度 768 维降采样/填充至 512 维与 pgvector 契合。

---

## [DEV-2026-10-07-03] Logbook V2 全维优化与智能协商自愈闭环 — ✅ 闭环

落实强制显式项目传参、三级交互协商自愈引擎、全维 MCP 2.x 规范升级。支持模糊拼写纠错（Did-You-Mean）与跨工作区越权熔断阻断。
1. 0 开放公网端口，原生走系统 Stdio 管道；2. 协商引擎兼顾智能纠错与权限硬边界；3. 扩充 Resources 只读快照与 Prompts 规程模板。

---

## [DEV-2026-10-07-02] Logbook 双平面架构实现与核心功能落地 — ✅ 闭环

实施 PostgreSQL 18 GA + pgvector + FastMCP 双平面隔离架构。完成四大安全防线（状态机不变量硬断言、敏感数据自动脱敏、原生 SNTP 探针、向量降级网关）。
1. 连接池硬锁 min_size=1, max_size=3，杜绝多连接争抢；2. Pydantic v2 校验 closed 必须提供证据锚点；3. Brix 历史资产无损迁移。

---

## [DEV-2026-10-07-01] Logbook 创世纪立项与规则初始化 — ✅ 闭环

用户令立项，旨在为自主 AI Agent 与研发团队构建原生、工业级的任务台账、排查手记与长期记忆中枢，彻底脱离传统 BBS 论坛数据结构。
1. 确立任务台账（tasks）、排查手记（devlogs）、架构铁律（rules）三大核心实体；2. 制定 DEVLOG.md 单文件状态追踪规程；3. 确立真实落地与知行合一铁律。

