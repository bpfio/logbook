# Changelog

本项目所有对外可见变更记录于此。格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [Unreleased]

> 依据设计正典 `docs/PLAN_OPT_2026-10-08.md`（六病一根图）三波实施预填；
> 各条目随对应波次验证门（pytest + 真机 MCP 回归 + QNAP 重建部署）通过后正式落版。

### Added
- `logbook_brief` 端点：单次调用返回全项目 open tasks/findings/waitings + running 批次 + 最近 N 条 devlog 标题的 brief digest，单包目标 ≤2K token（对照现状拼全貌 4-5 次查询 ≈5-6K token）。（P1-1，波2）
- `findings_bulk_upsert` / `waitings_bulk_upsert`：仿 `tasks_bulk_upsert` 的单事务批量落库原语。（P1-3，波2）
- 查询工具 `fields` 参数：字段投影，缺省返回精简列集，按需取全列。（P1-5，波2）
- 结构化错误面：全部工具异常统一收敛为 `{isError, error_type, detail}`（含 PG 约束名/诊断），含 `TASK_NOT_FOUND` 预检错误；根治 SDK "Error executing tool X" 裸包装。（P0-2，波1）
- devlog 写入 `source_rev` 溯源字段：快照漂移可机械检测。（P2 波3）
- embedding 降级路径服务端 WARN 日志（`vector_source` 标记已有，日志补齐）。（P0-5，波3）

### Fixed
- devlog 写路径统一 + 去重收口：`(project_id, task_id, COALESCE(title,''))` 唯一约束 + ON CONFLICT DO UPDATE，消灭 `import_brix.py` 直连 INSERT 旁路；存量 F14 ×4 副本数据清洗。（P0-1，波1）
- 状态机盖戳：findings `resolved_at` / waitings closed 时间戳 `COALESCE(旧值, now)`；tasks `closed_at` 不再被 EXCLUDED 反复刷新；补 planned→closed 直跳的 started_at 打点。（P0-3，波1）
- `devlog_search` 结果按 `(task_id, title)` 分组 top-1 去重兜底。（P0-4，波1）
- 裸错误收敛：`negotiation.py` 裸 ValueError / PermissionError 一并纳入结构化错误面。（P0-2，波1）
- 查询 LIMIT 参数化钳制（≤200）：`waiting_query` / `task_query` / `finding_query` 去除 f-string 拼接。（P1-4，波2）

### Security
- `deploy/qnap/compose.yaml` 明文星火 Key / DB 密码迁 `.env`，仓库仅保留 example 模板。（P2-1，波3）
- 删除 `ssh_server.py` 硬编码第二密码 "rekall"。（P2-2，波3）
- `export_markdown` 改为返回内容字符串，移除服务端写任意路径文件能力。（P2-4，波3）

### Changed
- 写响应瘦身：`*_record` / `*_upsert` 缺省仅回 `{ok, id, status}`，`full=true` 才回显全对象。（P1-2，波2）
- `export_markdown` 返回值语义变更：由"服务端落盘+返回路径"改为"返回内容字符串"（调用方自行落盘）。（P2-4，波3）
- pyproject 显式声明 asyncssh / click 依赖（现靠 Dockerfile 散装安装）。（P2-3，波3）

## [0.1.0] - 2026-10-07

### Added
- 双平面架构：PostgreSQL 18 GA + pgvector 底座，共享控制面 (`shared`) 与多租户数据面 (`proj_{name}`) 物理隔离。
- 三大正典实体：任务台账 (tasks) / 排查手记 (devlogs，根因四要素 + 512 维向量) / 工程铁律 (rules)。
- MCP 2.x 全维支持：13+ 工具、只读 Resources URI、规程 Prompts 模板。
- 三级交互协商自愈引擎：模糊拼写纠错 (Did-You-Mean)、物理工作区感知、跨工作区越权熔断。
- 安全防线：状态机不变量硬断言、公网 IP/Token 自动脱敏 (Sanitizer)、SNTP 高精度授时、向量降级网关。
- 批量原语 `tasks_bulk_upsert`：单事务批量落库；字段优化：parent_id / assignee / category。
- QNAP 生产部署：双容器内存硬限制 (app:96M/pg:48M)，实测常驻 ~63MB；Brix 历史资产无损迁移。
- 研发 CLI：doctor / status / show / import / export / mcp。
