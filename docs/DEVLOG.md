# LOGBOOK 开发日志 (DEVLOG) — 项目唯一开发日志与任务台账

> **规则 (AGENTS.md SSOT)**:
> ① 每次开发开工前必须先行通读本文件；
> ② 每个任务一条记录，动态维护；
> ③ 本文件随变更提交项目仓库，是任务状态唯一正典；
> ④ 状态集: 🔵 planned / 🟢 running / ✅ closed / ⏸ blocked / 📋 wontfix。
> ⑤ 排序: 最新在前。

---

## 一、任务台账 (11)

| ID | 状态 | 类型 | 标题 | commit | 备注 |
|---|---|---|---|---|---|
| L07 | ✅ closed | feat | 6大工程优化全量落地(统一返回包装/批量原语/向量缓存去重/参数归一化防呆) | f3a6599 | scripts/mcp_ingest_brix.py |
| L06 | ✅ closed | feat | MCP 协议自解释Schema升级、Brix 项目全量结构化对齐与向量化灌库 | f9edaf0 | scripts/import_brix.py |
| L05.4 | ✅ closed | deploy | QNAP 生产容器内存配额再平衡与全维度基准联测 | d27ac5f | deploy/qnap/compose.yaml |
| L05.3 | ✅ closed | fix | SSH MCP 孤儿子进程泄漏与内存膨胀根因定位与修复 | d27ac5f | src/logbook/ssh_server.py |
| L05.2 | ✅ closed | feat | 代码模型层与 MCP 工具全量对齐及测试套件扩充 | 04eab61 | tests/test_logbook.py |
| L05.1 | ✅ closed | feat | 字段扩展 DDL 建模与平滑迁移脚本编写执行 | 04eab61 | sql/02_optimize_fields.sql |
| L05 | ✅ closed | feat | 数据表字段深度优化、多Agent协同扩容与全量生产联调联测 | d27ac5f | tests/benchmark_live.py |
| L04 | ✅ closed | feat | QNAP 生产环境无缝上线、Rekall 彻底下线与星火向量生产验证 | 0477f75 | deploy/qnap/compose.yaml |
| L03 | ✅ closed | feat | 强制项目显式传参、三级交互协商自愈与全维 MCP 2.x 升级 | 5031c53 | tests/test_negotiation.py |
| L02 | ✅ closed | feat | 双平面隔离底座、PostgreSQL 18+pgvector、FastMCP 与 CLI 看板落地 | c51bbd7 | tests/test_logbook.py |
| L01 | ✅ closed | feat | Logbook 创世纪立项、规则制定与基础脚手架建立 | HEAD | README.md |

---

## 二、发现台账 (0)

| ID | 来源 | 级别 | 状态 | 处置 | 备注 |
|---|---|---|---|---|---|

---

## 三、待办/待用户 (0)

| ID | 类别 | 状态 | 事项 |
|---|---|---|---|

---

## 四、 批次演进记录

### [DEV-2026-10-07-07] 6大工程优化全量落地与 MCP 端到端闭环验证 — ✅ 闭环

- **任务源**: 响应用户指令，针对模拟传入测试中暴露的 6 大体验瓶颈实施全面优化，并经原生 MCP 协议客户端端到端压测验证。
- **已交付**:
  1. **查询返回格式统一包装**:
     - `task_query`, `waiting_query`, `finding_query`, `batch_query`, `devlog_search`, `rule_query` 统一封装为结构化对象 `{"success": true, "total": N, "project": proj, "items": [...]}`，杜绝多 TextContent 碎片化；
  2. **原子批量写入原语 (`tasks_bulk_upsert`)**:
     - 新增 MCP 工具，单次 RPC 结合数据库单事务原子批量落库数十项任务，耗时由数秒压制至 98ms (降低 90% 网络 RTT)；
  3. **DevLog 幂等 UPSERT 与向量哈希缓存**:
     - 手记入库时支持查重与文本对比；关键内容未变时自动更新元数据并复用既有 512 维向量 (`vector_source: "cached_skip"`)，彻底消除检索重复项并节省外部 API 配额；
  4. **入参轻量级归一化防呆 (`normalizer.py` + `BeforeValidator`)**:
     - 自动剔除 Emoji 并支持常见同义词映射 (`done`/`completed` -> `closed` 等)；
     - 基于 Pydantic v2 `BeforeValidator` 实现，既保留了自解释 Schema 枚举，又具备前置容错自愈能力；
  5. **非代码任务实测证据闭环解耦**:
     - 对 `drill` / `investigation` / `ops` / `docs` 放宽强绑 commit_hash 限制，自动将实测 `notes` 锚定为合规证据指针；
  6. **跨项目操作显式授权放行**:
     - 支持 `allow_cross_project=True` 与 `LOGBOOK_ALLOW_CROSS_PROJECT=1` 环境变量，兼顾防呆阻断与人工授权灵活性；
  7. **生产容器热重载与全量测试通过**:
     - 本地 18/18 单元与集成测试全绿通过；
     - 生产镜像更新并部署 QNAP，双容器物理内存常驻仅 42.19 MiB。

### [DEV-2026-10-07-06] MCP 协议自解释Schema升级与 Brix 项目全量结构化对齐入库 — ✅ 闭环

- **任务源**: 用户令经显式审批，全面升级 MCP 协议主动告知字段能力，无损组织并将 `bpfio/brix` 任务台账与排障手记结构化对齐入库。
- **已交付**:
  1. **MCP 协议层主动自解释能力**:
     - 在 `src/logbook/mcp_server.py` 中将核心状态、类型与优先级全面升级为 Python `typing.Literal` 强约束；
     - FastMCP 生成的标准 JSON Schema 现原生携带 `enum` 数组与字段级 `description`，Agent 端调用前即可获知完整字段要求；
     - 新增 `waiting_record` 工具与 `logbook://schema/fields` 数据字典资源；
  2. **双向转换管道 (converter.py) 批次无损支持**:
     - 升级 Markdown 逆向解析器，支持捕获 `## [DEV-...]` 批次段落并结构化解析为 `batches`；
     - 导入任务时自动关联当期 `batch_id`，对齐导出格式；
  3. **Brix 全量数据结构化对齐与向量化灌库**:
     - 编写幂等灌库脚本 `scripts/import_brix.py`；
     - 4 大批次入库 `brix.batches` (含 `DEV-2026-10-07-02` 停机保存态及方法论)；
     - 20 项任务入库 `brix.tasks` (全部关联批次，`F19` 置为 `blocked`，各任务分配责任人 Agent，锚定 commit hash)；
     - 91 项发现入库 `brix.findings` (含 F-320/F-321 阻塞项与 DRILL-F15 未结项)；
     - 14 项待办入库 `brix.waitings` (含 WAIT-F320 等待用户决策)；
     - 提炼 4 篇工业级排障手记入库 `shared.devlogs`，调用讯飞星火 MaaS API 实时计算 512 维向量 (实测余弦召回率 0.7957)；
  4. **跨项目读写隔离机制优化与镜像构建**:
     - 优化 `negotiation.py` 支持区分写操作（跨工作区强拦截）与只读操作（允许只读检索）；
     - 修复 `db.py` 切换事件循环时的连接池泄漏问题；
     - QNAP 生产环境无缝热重载，实测物理常驻内存降至 **64.4MB** (app: 42MB, pg: 22MB)。

### [DEV-2026-10-07-05] Logbook 字段深度优化、多Agent协同与生产联调验证 — ✅ 闭环

- **任务源**: 用户指令经正式审批（开发计划、修复过程全量同步到 Logbook 系统）。
- **已交付**:
  1. **数据模型与 Schema 深度优化**:
     - `tasks` 表：新增 `assignee`（责任主体/多 Agent 协同锁）、`parent_id`（支持长链路两级树形拆解）、`tags`（领域标签打标）与 `updated_at`（增量时戳与活跃监控）；
     - `devlogs` 表：新增 `author`（长期记忆归属溯源，区分 Agent/专家）与 `updated_at`；
     - `rules` 表：新增 `category`（领域分类 network/kernel/security/database 等，大幅减少 Prompt Token 噪音）；
     - `findings` 表新增 `reporter`；`waitings` 表新增 `owner`；
     - 编写完全向后兼容的平滑幂等迁移脚本 `sql/02_optimize_fields.sql`，动态巡检各 Schema 增量打补丁；
  2. **代码层与 MCP 2.x 工具全量对齐**:
     - `models.py` 与 `db.py` 严格转义双引号保护 `"{project}"`，彻底规避连字符项目名解析故障；
     - `mcp_server.py` 工具更新：`task_upsert` / `task_query` 支持 `assignee` 与 `parent_id` 过滤，`rule_query` 原生支持 `category` 领域筛选；
     - `task_timeline` 精确记录 `from_status` -> `to_status` 与操作员 `operator`；
  3. **生产参数联测与内存配额调优**:
     - 编写全链路生产联测工具 `tests/benchmark_live.py` 覆盖 8 大维度，实测 PG 18.6 GA 建连延迟 25.29ms，星火向量 API 响应正常，余弦召回度 0.8316；
     - 深入排查发现 AsyncSSH 处理 Stdio MCP 时未写 EOF 导致子进程孤儿泄漏与内存触顶 95.83MB；在 `pipe_streams` 增加 EOF 写入并在 `handle_ssh_process` 增加超时强制清理，彻底根治孤儿进程；
     - 清理宿主机冗余 `rekall` MCP 配置，`logbook-app` 内存回落并稳定在 **46.41MB**；
     - 在保持宿主机总 144MB 配额绝对不变的前提下，再平衡配额为 `app: 96MB / postgres: 48MB`，彻底消除生产 OOM 风险；
  4. **全量测试套件保障与系统同步**:
     - 扩展单元测试与集成测试断言，14 项测试 100% 通过（4.73s）；
     - 通过 MCP 接口将全部 9 项任务及 3 篇深度排查手记（故障四要素 + 星火向量）同步入库生产知识库。
- **判据与实测**:
  - `pytest tests/`: 14 passed in 4.73s；
  - `devlog_search`: 精准按余弦相似度召回新增排查手记（得分 0.7698）。

### [DEV-2026-10-07-04] Logbook QNAP 生产环境部署与全栈替换上线 — ✅ 闭环

- **任务源**: 用户指令经正式审批（路线 A：彻底替换升级为 Logbook，回收生产节点 IP，配置讯飞星火向量，绝对保障 QNAP 其他服务安全）。
- **已交付**:
  1. **生产镜像容器化**: 编写 `Dockerfile`（基于 `python:3.13-slim` + `uv` 极速构建 + 纯 Python `asyncssh`），本地秒级构建出纯净轻量镜像 `logbook:latest` 并流式载入 QNAP Docker；
  2. **Rekall 优雅下线**: 优雅停止并移除旧 `rekall-app` 与 `rekall-postgres` 容器，释放节点 IP 及端口；
  3. **绝对隔离与零冲击**: QNAP 上原有服务（`gitea`、`fastapi-dls-1`、`kms-1`）全程 100% 隔离运行未受干扰；
  4. **Logbook 生产双容器 Pod 上线**:
     - 在 QNAP `/share/CACHEDEV1_DATA/Container/logbook/` 部署 `compose.yaml` 与 `01_init.sql`；
     - 宿主机双容器常驻实测仅 **~63MB 物理内存**（`logbook-app` 41MB，`logbook-postgres` 22MB），远低于配置的 144MB 配额；
  5. **Brix 数据全量无损迁移**: 成功将 Brix DEVLOG 历史数据（20 任务、91 发现、14 待办）全量导入 QNAP PostgreSQL 18.6 生产底座；
  6. **MCP 管道与星火向量生产验证**:
     - 本地 `~/.gemini/config/mcp_config.json` 免密 SSH 管道全通；
     - 端到端实测 `devlog_record` 与 `devlog_search`：调用讯飞星火 MaaS API 生成 512 维向量，入库与语义召回匹配度达 0.8144，全流程全绿。
- **判据与实测**:
  - `ssh sysadmin@<server-ip> "logbook doctor"`: PG 18.6 连接正常、Asia/Shanghai 时区正常、pgvector 扩展就绪；
  - `devlog_record` -> `vector_source: "spark_maas"`, `isError: false`；
  - `devlog_search` -> 精确召回目标手记，得分 0.8144。

### [DEV-2026-10-07-03] Logbook V2 全维优化与智能协商自愈闭环 — ✅ 闭环

- **任务源**: 用户指令与审定 V2 方案，落实强制显式项目传参、三级交互协商自愈引擎、全维 MCP 2.x 规范升级。
- **已交付**:
  1. **智能交互协商引擎**: 编写 `src/logbook/negotiation.py`，实现 `difflib` 智能模糊纠错与工作区事实核对。当用户或 Agent 错传 `briz` 时，自动返回包含相近候选 `['brix']` 与物理工作区证据的结构化拒绝响应，驱动 Agent 0 人工干预自愈重试；若跨项目越权（在 `logbook` 工作区操作 `brix`）坚决触发 `PermissionError` 熔断拦截；
  2. **强制显式传参契约**: 重构 `src/logbook/mcp_server.py`，所有 8 个核心工具中 `project: str` 必须作为第一入参，彻底消灭隐式脑补；
  3. **全维 MCP 2.x 原语引入**:
     - Resources（只读上下文）: 注册 `logbook://{project}/tasks/active`、`logbook://{project}/waitings/open` 与 `logbook://rules/engineering` URI，Agent 0 工具开销挂载直读；
     - Prompts（规程模板）: 注册 `/start_batch`（开工必读台账）与 `/record_devlog`（根因四要素）标准模板；
  4. **脱敏引擎加固**: `src/logbook/sanitizer.py` 扩充私有 IPv6（ULA / Link-local）自动转换为 RFC 3849 保留地址（`2001:db8::x`）；
  5. **CLI 看板体验升维**: `src/logbook/cli.py` 增加交互式模糊纠错提示；
  6. **测试套件扩充与全绿**: 编写 `tests/test_negotiation.py`，全量测试套件增至 14 项，100% 通过（耗时 4.70s）。
- **判据与实测**:
  - `pytest tests/`: 14 passed in 4.70s；
  - `logbook status briz`: 正确提示相近合法项目 `['logbook', 'brix']`；
  - `logbook doctor`: 数据库时区 Asia/Shanghai 正常、时钟漂移 -0.337s 正常、内存 43.7MiB / 80MiB 正常。

### [DEV-2026-10-07-02] Logbook 双平面架构实现与核心功能落地 — ✅ 闭环

- **任务源**: 用户指令与审定方案，实施 PostgreSQL 18 GA + pgvector + Python 3.14 FastMCP 双平面隔离架构。
- **已交付**:
  1. **物理底座与轻量锁定**: 编写 `docker-compose.yml`，锁定 PostgreSQL 18.6 + pgvector，内存硬锁 80MB，实测常驻仅 42MiB，连接池收敛为 2~3 个；
  2. **双平面 DDL 建模**: 编写 `sql/01_init.sql`，落地 `shared` 知识面（规则表与排查手记）与项目 Schema 模板函数 `shared.init_project_schema`（完全隔离各项目任务与发现）；
  3. **四大安全与可靠防线**:
     - 状态机不变量硬断言：Pydantic v2 模型 `Task` 强制 closed 状态必须提供 commit_hash 或 proof_link 证据锚点；
     - 敏感数据自动脱敏：`sanitizer.py` 自动清洗私有 IPv4 (转 RFC 5737 占位符) 与各种 API Token；
     - 原生 SNTP 探针：`time_sync.py` 纯 socket 对接国家/阿里授时中心，漂移超阈值告警，输出标准北京时间；
     - 异步数据库驱动：`db.py` 纯二进制协议，自适应处理 pytest 跨循环重置；
     - 向量降级网关：`vector.py` 512 维向量接口，内置超时自动降级至 tsvector 全文检索；
  4. **原生 FastMCP Server**: `mcp_server.py` 暴露 8 大原子标准工具，0 公网暴露面；
  5. **CLI 运维与双向 Markdown 转换**: `cli.py` 提供 `doctor/status/show/import/export`，实测无损导入 Brix 项目原有的 21 条任务、91 条发现与 14 条待办并成功反向还原；
  6. **测试套件全绿**: `tests/test_logbook.py` 与 `tests/test_mcp.py` 共 10 项测试全数通过（用时 5.04s）。
- **判据与实测**:
  - `logbook doctor`: 数据库连接通过、时钟同步正常（漂移 -0.318s）、容器内存 41.7MiB / 80MiB 通过；
  - `pytest tests/`: 10 passed in 5.04s；
  - `logbook status brix`: 21 项任务与 14 项阻塞展示无瑕疵。

### [DEV-2026-10-07-01] Logbook 创世纪立项与规则初始化 — ✅ 闭环

- **任务源**: 用户令立项，旨在为自主 AI Agent 与研发团队构建原生、工业级的任务台账、排查手记与长期记忆中枢，彻底脱离传统 BBS 论坛数据结构。
- **已交付**:
  1. 远端与本地仓库创建：GitHub `bpfio/logbook` 公开仓库就绪，本地目录 `/home/yupeng/logbook` 绑定；
  2. 智能体正典定义：编写 `AGENTS.md`，确立任务台账（tasks）、排查手记（devlogs）、架构铁律（rules）三大核心领域实体；
  3. 任务台账正典建立：建立 `docs/DEVLOG.md` 单文件状态追踪与多 Agent 协作规程；
  4. 架构宣言与愿景：编写 `README.md`，明确阐述相对 Jira/BBS/静态 Markdown 的核心优势与架构拓扑。

