# 📌 Logbook / cliserver 会话全量状态存档 (Session State Archive)

**记录时间**：2026-10-08 18:05 (UTC+8)  
**批次归属**：`DEV-2026-10-08-12`  
**关联任务**：`L12`（状态：`closed`）  
**手记凭证**：`DevLog #28`（已写入生产 PostgreSQL + 512 维向量索引）  
**当前系统健康度**：**100 / 100 满分**（Agent 具备自主自开户与双轨寻址能力、41 项自动化测试全绿、QNAP 容器与数据库热升级就绪、SSH MCP 生产链路端到端闭环）

---

## 一、 会话核心目标与成果总览

1. **宿主身份确证**：
   - 彻底梳理网络拓扑与主机命名，确证当前运行主机 `xhub`（`192.168.1.22` / `192.168.200.22`）即研发正典与密钥标识中定义的本地开发机 **`cliserver`**；
   - 确证 Agent 当前直接原生运行于 `cliserver`，具备原生终端、代码编辑与免密执行能力，无需外部跳转登录。

2. **Git 仓库权威锚定与双轨寻址 (Dual-Track Resolution)**：
   - 执行 `sql/04_project_registry.sql` 建立 `shared.projects` 登记中心，以 Git 仓库坐标 (`owner/repo`，如 `bpfio/brix`, `bpfio/logbook`, `io/TS`) 为权威 SSOT 标识；
   - 引入 `slug_to_schema_name` 安全映射，物理 DB Schema 严格遵循合规小写标识符规范；
   - 数据库与智能协商层全面打通双轨解析：无论传入 `bpfio/brix` 还是短名 `brix`，均可透明解析至物理 Schema `brix`。

3. **Agent 自开立原语 (`project_init`) 与两态自愈协商**：
   - 实现并上线 `project_init(repo, title, description)` 原语，使 Agent 在接入全新项目时具备受控自开立与幂等建库能力；
   - 优化 `negotiation.py`，智能区分两态：
     - **状态 A**（相似度 ≥ 0.7，如 `briz`）：判定为输入手滑，返回 `CORRECT_PROJECT_PARAMETER` 纠偏建议；
     - **状态 B**（无相似项目，如全新仓库 `io/TS`）：判定为新项目，返回 `PROJECT_INIT_NEEDED`，并直接提供一键可执行的 `project_init` 调用参数引导。

4. **架构加固与生产发布**：
   - 修复无 Git 目录环境下（如 Docker 容器 `/app`）工作区误锁定的问题；
   - 调整 FastMCP 工具装饰器顺序为 `@mcp.tool()` 在外、`@tool_shell` 在内，保障 100% 结构化错误兜底；
   - 本地全量测试 41/41 100% 全绿通过；
   - QNAP 生产镜像完成重建与热重载，真实调用 `project_init(repo="io/TS")` 成功开辟物理 Schema `ts` 并写入任务。

---

## 二、 节点拓扑与环境映射 (Host & MCP SSOT)

| 节点角色 | 主机名 / 容器 | IP 地址 | 访问方式与通道职责 |
|---|---|---|---|
| **本地开发机** | `xhub` / `cliserver` | `192.168.1.22`<br>`192.168.200.22` | • 当前会话原生宿主，本地用户 `yupeng`<br>• SSH 公钥注释 `yupeng@cliserver`<br>• `agy` CLI 宿主环境，全局发现 `~/.gemini/config/mcp_config.json` |
| **Logbook App 容器** | `logbook-app` | `192.168.1.68` | • 运行于 QNAP NAS Container Station（macvlan 直通）<br>• 暴露 SSH Stdio MCP 管道 (`sysadmin@192.168.1.68 mcp`)<br>• 内存硬配额 96MB，常驻 ~40MB |
| **Logbook PG 容器** | `logbook-postgres` | `192.168.1.68` | • 共享 `logbook-app` 网络栈 (`127.0.0.1:5432`)<br>• PostgreSQL 18 + pgvector 插件<br>• 权威项目注册中心 (`shared.projects`) + 多项目独立 Schema |
| **存储宿主机** | QNAP TS-453D | `192.168.1.33` | • 承载 Docker 编排与持久化存储卷 |

---

## 三、 `agy` MCP 部署配置与发现规范

### 1. 全局配置文件
- **路径**：`~/.gemini/config/mcp_config.json`
- **范围**：全局生效（Machine-Local Global），`agy` 在任意目录运行均自动挂载此配置。
- **配置内容**：
```json
{
  "mcpServers": {
    "logbook": {
      "command": "ssh",
      "args": [
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=no",
        "sysadmin@192.168.1.68",
        "mcp"
      ]
    }
  }
}
```

### 2. 状态验证命令
```bash
# 1. 检查 CLI 注册状态
agy mcp list
# 输出: logbook  stdio  enabled  ssh -o BatchMode=yes -o StrictHostKeyChecking=no sysadmin@192.168.1.68 mcp

# 2. 检查本地 SSH 免密连通性
ssh -o BatchMode=yes sysadmin@192.168.1.68 "mcp"
```

---

## 四、 已就绪的 15 个 Logbook MCP 原语与资源矩阵

| 模块 | 原语 / 资源名称 | 功能描述 |
|---|---|---|
| **项目开户** | `project_init` | 初始化并开立新项目的台账空间（DDL Schema 幂等创建与双轨注册） |
| | `logbook://projects` | 只读资源：所有已注册项目的 Git 坐标与 Schema 清单 |
| **任务台账** | `task_upsert` | 原子登记或推进任务状态机（支持 P0~P3、闭环证据断言） |
| | `tasks_bulk_upsert` | 单事务批量写入任务清单（高效导入） |
| | `task_query` | 多维度查询任务看板（支持状态、优先级、责任人过滤） |
| **根因手记** | `devlog_record` | 结构化录入排查手记（强制四要素 + 512维向量 + 幂等去重） |
| | `devlog_search` | 语义向量与全文混合检索历史排查经验 |
| **缺陷发现** | `finding_record` | 记录演练或代码审查发现的缺陷与风险 |
| | `finding_query` | 查询项目未决缺陷列表 |
| **阻塞待办** | `waiting_record` | 记录阻塞项或待用户裁决事项 |
| | `waiting_query` | 查询未关闭的阻塞项 |
| **研发提要** | `logbook_brief` | 极速获取项目开工简报 Digest（低 Token 消耗） |
| **正典与导出** | `rule_query` | 查询跨项目统一的架构铁律与工程红线 |
| | `batch_upsert` | 登记研发批次演进记录与排障方法论 |
| | `batch_query` | 查阅项目研发批次看板 |
| | `export_markdown` | 从数据库动态导出标准 DEVLOG.md |

---

## 五、 生产数据入库凭证

1. **批次登记**：
   - 批次 ID：`DEV-2026-10-08-12`
   - 标题：`Git仓库坐标权威锚定、双轨项目协商与自愈开户原语落地`
   - 状态：`completed`

2. **任务登记**：
   - 任务 ID：`L12`
   - 类型：`feat`，优先级：`P1`，状态：`closed`
   - 证据锚点：`proof_link: tests/test_project_registry.py + QNAP实测PASS`

3. **排查手记**：
   - 手记 ID：`28`
   - 标题：`Agent自开立与双轨项目解析架构落地 (SSOT Git 坐标 + 智能协商自愈)`
   - 向量索引状态：`vector_source: spark_maas`（512 维向量已落库）
