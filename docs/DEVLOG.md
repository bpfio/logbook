# Logbook 开发日志 (DEVLOG) — 唯一任务台账与批次正典

> **规则 (AGENTS.md §六)**:
> ① 每次开发（夜批/日批/单任务）开工前**必须先行通读本文件**；
> ② 每个任务一条记录（开工→🟢 running；完成/停止→更新状态与证据指针），动态维护；
> ③ 本文件随变更**提交项目仓库**，是任务状态唯一正典——不按天另立清单；
> ④ 状态集: 🔵 planned / 🟢 running / ✅ closed / ⏸ blocked(待用户/待外部) / 📋 wontfix。
> ⑤ 排序: 最新在前。

---

## 一、 任务台账 (2)

| ID | 状态 | 优先级 | 类型 | 标题 | commit | 备注 |
|---|---|---|---|---|---|---|
| L02 | closed | P1 | feat | 双平面隔离底座、PostgreSQL 18+pgvector、FastMCP 与 CLI 看板落地 | pending | 测试 10/10 全绿，Brix 数据无损导入导出核销 |
| L01 | closed | P1 | feat | Logbook 创世纪立项、规则制定与基础脚手架建立 | main | 仓库创建与 AGENTS.md / README / DEVLOG 初始化 |

---

## 二、 发现台账 (0)

| ID | 来源 | 级别 | 状态 | 处置 | 备注 |
|---|---|---|---|---|---|
| - | - | - | - | - | 当前无未决缺陷 |

---

## 三、 待办 / 待用户 (0)

| ID | 类别 | 状态 | 事项 |
|---|---|---|---|
| - | - | - | 当前无阻塞项 |

---

## 四、 批次演进记录

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
- **下一步待办**:
  - 设计 PostgreSQL 17 + pgvector 极简 DDL 物理建表脚本；
  - 编写核心数据模型（Pydantic v2）与数据库连接池层；
  - 实现原生 SSH / Stdio JSON-RPC 2.0 MCP 适配器。
