# Logbook 稳定性整改会话状态（2026-10-13）

## 1. 会话定位

- 基线：`main@149a646e3861` 之上的未提交工作区。
- 关联任务：`L-STAB`（修复）、`L-AUD-01`（首轮审计）、`L-AUD-02`（变更后复审）。
- 权威文档：
  - 首轮审计：`docs/AUDIT_LOGBOOK_STABILITY_2026-10-13.md`
  - 修复计划：`docs/PLAN_STABILITY_FIX_2026-10-13.md`
  - 变更后复审：`docs/AUDIT_LOGBOOK_STABILITY_REAUDIT_2026-10-13.md`
  - 任务台账：`docs/DEVLOG.md`
- 当前裁决：代码与本地/隔离环境验证完成；QNAP S5 生产验收和提交证据未完成，任务保持 `running`，不得关闭。

## 2. 已完成的代码修复

### A-01 版本化迁移链

- 新增 `src/logbook/migrate.py`，支持 `status/up/verify`。
- 支持 SHA256 checksum、`public.schema_migrations`、PostgreSQL advisory lock。
- 支持空库迁移、既有库采用、重复执行 no-op、并发迁移和失败事务回滚。
- 当前迁移共 8 个，新增 `sql/08_message_read_audit.sql`。
- `Dockerfile` 复制 `sql/`；Compose 使用完整迁移目录；SSH 启动前执行 `up_with_retry()`。

### A-02/A-03/A-04 并发与稳态性能

- `Database.connect()/close()` 使用 loop-aware 单飞锁。
- 项目 Schema、消息 Schema、调研 Schema 使用首检锁与 ready 缓存。
- 文件租约使用单条条件 UPSERT；冲突详情由同一 CTE 返回，消除二次读取竞态。
- 双 Agent 文件租约并发 100 轮，每轮唯一成功者。
- 连接池与 DDL 首检并发测试均只执行一次。

### A-05 SSH/MCP 生命周期

- 移除固定 `sleep(0.3)` 路径。
- MCP bridge 根据 JSON-RPC envelope ID 跟踪待回请求，客户端 stdin EOF 后延迟关闭子进程 stdin。
- 真实 AsyncSSH 2.24.1 验证 5.2 秒慢调用、5001 行输出、客户端 EOF 和子进程回收。
- 默认 SSH 监听回环地址；QNAP 示例显式配置局域网监听。

### A-06/A-07 信箱与 SSH 安全

- 新增统一可信身份解析 `_trusted_agent_identity()`。
- `message_read`、`message_inbox`、个人信箱 Resource、`message_send`、`agent_message_send`、`lease_acquire`、`lease_release` 均拒绝调用参数冒充。
- 信件读取 SQL 限定 `id + to_agent`，并记录 `read_by_agent`。
- SSH 默认关闭口令认证；缺少有效公钥时拒绝启动；弱口令和已知默认口令拒绝。
- SSH principal 映射为可信 `LOGBOOK_AGENT_ID`。

### A-08 可复现门禁

- `scripts/gate.sh` 支持 `unit|integration|full`。
- 新增 `deploy/test/compose.yaml`、`scripts/test_db.sh` 和 pytest integration marker。
- 门禁自动导出 `PYTHONPATH=src`，使用 uv 创建隔离环境并安装 runtime/dev 依赖。
- 部署测试只读取 Git 跟踪的 example 文件。

## 3. 复审发现与处置

| 发现 | 首次级别 | 当前状态 | 证据 |
|---|---|---|---|
| inbox/Resource/send 身份绕过 | P1 | fixed | 跨 Agent 拒绝矩阵集成测试通过 |
| 租约持有者身份可冒充 | P1 | fixed | 非持有者续租/释放返回 `CALLER_IDENTITY_MISMATCH` |
| QNAP 生产信箱故障未关闭 | P1 | open | `L-MSG-1` 仍为 P1，S5 未执行 |
| 运行时 DDL 与迁移漂移 | P2 | fixed | 未迁移数据库被明确拒绝，不再执行第二份 DDL |
| clean-checkout 门禁失败 | P2 | fixed | 无 `.git` 隔离树自动 bootstrap 并全绿 |
| 租约冲突二次读取竞态 | P3 | fixed | 单条 CTE + 100 轮并发通过 |

Logbook 已登记：

- `RA-IDENT-1`：P1，fixed。
- `RA-QUAL-1`：P2，fixed。
- `RA-LEASE-1`：P3，fixed。
- `L-MSG-1`：P1，planned/open，生产阻断。

## 4. 当前验证结果

### 当前工作区

- unit：`45 passed`。
- integration：`40 passed`。
- 历史写入重放：23 项判定全部通过，零副本膨胀。
- brief：单调用约 269 token，低于 2000 token 门限。
- 只读去重检查通过。

### 隔离 clean-checkout

- 将全部跟踪文件及相关未跟踪修复文件复制到无 `.git` 的临时目录。
- `./scripts/gate.sh full` 自动 bootstrap 后：
  - unit `45 passed`；
  - integration `40 passed`；
  - 重放 `23/23`；
  - brief `269 token`；
  - 去重 dry-run 通过。

### 静态检查

- `git diff --check` 通过。
- `compileall` 覆盖 `src/ tests/ scripts/` 通过。
- QNAP `compose.yaml.example` 配置解析通过。
- 测试 PostgreSQL 容器已停止。

## 5. 剩余阻断与下一步

1. 完成 S5 生产升级前备份和 checksum。
2. 构建并保留新旧镜像标签。
3. 对既有 QNAP 数据卷执行迁移前只读探测和迁移状态报告。
4. 升级后执行 25 Tools、8 Resources、4 Prompts、信箱、租约和 5 秒慢调用回归。
5. 执行数据量对账、连接/锁等待、RSS、线程数和 EOF 后时延观测。
6. 验证旧镜像或备份回滚路径。
7. 完成后补充真实 commit hash 和验证报告指针，再关闭 `L-STAB`、`L-AUD-01`、`L-AUD-02`。

## 6. 工作区与安全边界

- 当前没有 Git commit，也没有创建新分支。
- 未读取、未提交未跟踪的数据库备份文件 `deploy/qnap/logbook_backup_20261009_153333.dump`。
- 未连接生产 PostgreSQL、QNAP 或真实生产 SSH。
- 测试仅使用本地临时 PostgreSQL、回环地址和占位凭据。
- 本地完整 Compose 与历史私网记录不得复制到新提交内容或公开输出。
