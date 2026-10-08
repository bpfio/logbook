# LOGBOOK 开发日志 (DEVLOG) — 项目唯一开发日志与任务台账

> **规则 (AGENTS.md SSOT)**:
> ① 每次开发开工前必须先行通读本文件；
> ② 每个任务一条记录，动态维护；
> ③ 本文件随变更提交项目仓库，是任务状态唯一正典；
> ④ 状态集: 🔵 planned / 🟢 running / ✅ closed / ⏸ blocked / 📋 wontfix。
> ⑤ 排序: 最新在前。

---

## 一、任务台账 (12)

| ID | 状态 | 类型 | 标题 | commit | 备注 |
|---|---|---|---|---|---|
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

## 二、发现台账 (5)

| ID | 来源 | 级别 | 状态 | 处置 | 备注 |
|---|---|---|---|---|---|
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

## L09 | 优化设计开账 (2026-10-08, 用户令启动) — 🟢 设计正典
- 设计正典 = docs/PLAN_OPT_2026-10-08.md (六病一根图 / P0 正确性五件 / P1 效率五件 / P2 安全五件 / 三波实施+验证门)。依据=两份深探报告 (架构 17 切入点+数据层 7 节根因) + brix 夜11/12 真实调用 25+ 次体感。实施待用户令逐波发车。
## L10 | 优化升级批开账 (2026-10-08, 用户令启动) — 🟢 五线并行
- 升级批按 PLAN_OPT_2026-10-08 三波实施，五线 A1-A5 并行: A1 数据/去重/盖戳、A2 错误面/协商、A3 部署/安全面、A4 brief/bulk/效率面、A5 文档与发布。验收门 = 每波 gate (pytest + 真机 MCP 回归 + QNAP 重建)；A5 本批交付 README 工具清单更新、CHANGELOG.md 新建 (Unreleased 按 P0/P1/P2 预填，建议版本 0.2.0)、deploy/qnap/DEPLOY.md 升级 runbook。文档中依赖 A1-A3 落地项 (sql/03_dedup、scripts/replay_brix_writes.py、brief 端点实测 token 数) 标注为待定稿。
- **L10 收口 (10-08 12:26)**: 五线 (A1 数据层/A2 工具壳错误面/A3 安全包/A4 Harness/A5 文档) 全量落地并完成生产发布。
  1. **QNAP 生产部署**: 宿主机备份先行 (`logbook_pre_20261008_041759.dump`, 96KB)；构建 `logbook:0.2.0` 镜像并流式加载；`compose.yaml` / `.env` 配置同步平滑重拉双容器；
  2. **数据迁移**: `sql/03_dedup.sql` 成功清洗 12 条存量副本，创建 `uq_devlogs_proj_task_title` 唯一表达式索引，实测幂等；
  3. **真实回归**: 端到端生产 SSH MCP 通道重放 18 笔真实写入 (23 笔判定 100% PASS，0 副本产生)；`logbook_brief` 生产端点实测 224~825 Token (省 93%)；`TASK_NOT_FOUND` 写前预检拦截实测通过；
  4. **宿主隔离**: QNAP 宿主现有容器 (Gitea 等) 100% 隔离运行不受影响；双容器总内存实测压制在 71.4MB。0.2.0 正式上线闭环。

