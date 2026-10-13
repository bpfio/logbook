# Logbook 稳定性整改变更后复审报告（2026-10-13）

| 项目 | 结论 |
|---|---|
| 审计对象 | `main@149a646e3861` 之上尚未提交的稳定性整改工作区 |
| 关联任务 | `L-AUD-02`；原报告 `docs/AUDIT_LOGBOOK_STABILITY_2026-10-13.md` |
| 审计范围 | A-01 至 A-08 复验、变更代码审查、隔离数据库对抗测试、真实 AsyncSSH 测试、当前树与隔离树门禁 |
| 总体结论 | 首次复审发现的可信身份、Schema 漂移和 clean-checkout 缺口已在本轮二次修复；生产 S5 仍未验收，暂不批准生产发布或关闭 `L-STAB` |
| 发现分级 | 首次复审 P0×0；P1×3；P2×2；P3×1；二次修复后仅剩生产 S5 发布阻断 |

## 一、复审结论

本轮不是重复首轮审计，而是针对未提交变更进行的代码与运行态复验。

- **A-01、A-03、A-04、A-05、A-07 本地修复基本成立**：版本化迁移、连接池单飞、项目缓存、协议生命周期和 SSH 公钥认证均有可重复验证证据。
- **A-02 只证明协作型 Agent 之间的数据库原子互斥**，未证明恶意或错误 Agent 不能冒充持有者；因此租约闭环为“部分修复”。
- **A-06 仅修复 `message_read` 单点**，`message_inbox`、个人信箱 Resource 和发信身份仍接受调用参数，存在完整读取绕过；因此邮箱鉴权闭环失败。
- **A-08 尚未完成 clean-checkout 验收**：当前工作区门禁全绿，但隔离复制的工作树在无项目虚拟环境时因缺少 `PYTHONPATH` 和运行时依赖直接失败。
- **S5 生产验收仍缺失**，且 Logbook 台账中的 `L-MSG-1` 仍为 P1 planned、相关发现仍为 open，说明生产 `message_*` 故障没有正式关闭证据。

## 二、新增或未闭环发现

### R-01：邮箱列表与 Resource 绕过收件人鉴权（P1，发布阻断）

**证据**

- `message_read` 已使用可信 `LOGBOOK_AGENT_ID` 并限定 `to_agent`。
- `message_inbox(agent_name=...)` 仍直接查询任意 Agent 的信件，并返回完整正文。
- 个人信箱 Resource 也通过 URL 中的 `agent_name` 查询任意信箱。
- 临时 PostgreSQL 对抗测试以可信身份 `eve` 调用 `message_inbox(agent_name="bob")`，成功返回 Bob 的完整私信正文。

**影响**

任何可调用 MCP 的 Agent 无需猜信件 ID，即可批量读取其他 Agent 的私信；首轮 A-06 的读取越权并未真正关闭。

**修复要求**

1. 建立统一的可信身份解析函数；SSH principal 映射结果作为唯一身份来源。
2. `message_inbox`、个人信箱 Resource、`message_read` 统一禁止参数覆盖可信身份。
3. `message_send` 的 `from_agent` 只能作为兼容参数，必须等于可信身份。
4. 增加跨 Agent inbox、Resource、send 和 read 的矩阵测试。

### R-02：文件租约持有者身份可被冒充（P1，发布阻断）

**证据**

- `lease_acquire` 和 `lease_release` 直接使用工具参数 `agent_name`。
- 数据库条件更新以“相同 `agent_name` 可续租/释放”为合法分支。
- 对抗测试中可信身份 `eve` 传入 `agent_name="trusted_holder"` 后，成功续租并释放其他 Agent 的租约。

**影响**

租约 SQL 本身是原子的，但在多 Agent 信任边界内不能防止持有者冒充；错误或恶意 Agent 可续租、释放他人施工权，A-02 的协作正确性不能作为最终闭环证据。

**修复要求**

1. `agent_name` 与可信身份不一致时直接拒绝。
2. release 必须按数据库中的持有者身份核验，而不是接受任意名字。
3. 增加“非持有者续租失败、非持有者释放失败、过期接管成功”测试。

### R-03：生产信箱故障与 S5 验收仍未关闭（P1，发布阻断）

**证据**

- Logbook 任务 `L-MSG-1` 当前为 P1 planned。
- 对应生产发现仍为 open，记录 QNAP 上 `message_inbox`、`message_send` 等调用断连，而其他工具正常。
- 本轮修复只在本地临时 PostgreSQL 和本地 AsyncSSH 通过，未部署至生产卷，也未完成备份、对账、回滚演练。

**影响**

无法证明新迁移链、SSH 生命周期和信箱修复解决了生产实际故障，也无法证明升级不会损坏既有数据。

**修复要求**

完成 S5：生产前备份与 checksum、隔离镜像冒烟、旧新镜像并存、迁移状态核验、信箱/租约/慢调用回归、数据量对账和回滚演练。

### R-04：运行时 DDL 与版本迁移出现 Schema 漂移（P2）

**证据**

- `sql/08_message_read_audit.sql` 添加 `read_by_agent`。
- `ensure_messaging_schema()` 的内联建表仍不包含该列。
- 对抗测试在已有 `shared` Schema 的空库上只执行 `ensure_messaging_schema()`，写信成功，但读信抛出 `UndefinedColumnError: column "read_by_agent" does not exist`。

**影响**

`LOGBOOK_AUTO_MIGRATE=0`、测试夹具或旧入口绕过迁移时，运行时自愈路径会建立旧结构并随后读取新列，形成双 Schema SSOT。

**修复要求**

优先让所有结构变更只进入版本迁移；若保留运行时首检，必须复用同一迁移清单或在启动时拒绝未迁移数据库，不能再维护另一份 DDL。

### R-05：clean-checkout 门禁仍不可复现（P2）

**证据**

- 当前工作区 `./scripts/gate.sh full` 全绿。
- 将所有跟踪文件与相关未跟踪修复文件复制到隔离目录后，同一门禁失败。
- 失败包括 `ModuleNotFoundError: logbook`、`asyncpg`、`mcp`、`asyncssh`。
- `gate.sh` 只检查 `pytest`，没有设置 `PYTHONPATH=src`，也没有验证全部运行时与开发依赖。

**影响**

A-08 仍不满足“新克隆一条命令完成全量门禁”；当前全绿依赖开发者已有 `.venv` 和 editable install。

**修复要求**

门禁显式导出 `PYTHONPATH="$REPO_ROOT/src"`，逐一检查 runtime/dev imports，并提供明确的 bootstrap 命令或使用 `uv run`。

### R-06：租约冲突详情存在低概率二次读取竞态（P3）

`acquire_file_lease()` 在条件 UPSERT 未返回行后先读取冲突者，再二次读取完整 Lease。若期间租约被释放，第二次读取可能返回空并触发 `FileLease(**None)`。原子获取不受影响，但错误返回路径可能抛异常；应使用单次 CTE/FETCH 语句返回冲突信息。

## 三、已复验通过项

| 首轮项 | 复审结果 | 关键证据 |
|---|---|---|
| A-01 | 本地通过 | 8 个迁移；空库两次执行、既有库采用、并发迁移锁通过；失败迁移只标记成功的 01，checksum 改动被拒绝 |
| A-02 | 部分通过 | 条件 UPSERT 有效；2 Agent 100 轮唯一成功；身份冒充见 R-02 |
| A-03 | 通过 | 3 个并发首调用只创建 1 个池，无泄漏；消息/项目 DDL 首检并发只执行一次 |
| A-04 | 通过 | 项目 ready 缓存使常规查询不再执行 DDL；仍有 R-04 的双 Schema 风险 |
| A-05 | 通过 | 固定 300ms 已移除；真实 AsyncSSH 5.2 秒慢调用跨 stdin EOF 完整返回，无子进程残留；5001 行输出压力测试无尾部丢失 |
| A-06 | 失败 | `message_read` 已修复，但 inbox/Resource/send 绕过见 R-01 |
| A-07 | 通过 | 默认关闭口令认证、弱口令拒绝启动、默认回环监听、真实公钥握手通过 |
| A-08 | 失败 | 当前树全绿，但隔离树门禁失败，见 R-05 |

## 四、门禁与静态验证

- 当前工作区全量门禁：unit `44 passed`、integration `38 passed`。
- 18 笔历史写入重放：23 项判定全部通过，零副本膨胀。
- brief 单调用约 269 token，低于 2000 token 门限。
- `git diff --check`、Python `compileall`、QNAP Compose example 配置校验通过。
- 隔离树全量门禁：失败；未将其误记为 A-08 闭环。

## 五、发布裁决与修复顺序

1. **立即修复 R-01、R-02**：统一可信身份边界，补齐跨 Agent 拒绝矩阵。
2. **随后修复 R-04、R-05**：收敛 Schema SSOT，恢复 clean-checkout 可复现性。
3. **处理 R-06**：消除租约错误返回路径的二次读取竞态。
4. **执行 R-03 / S5**：仅在上述 P1/P2 修复与本地门禁通过后升级 QNAP。
5. 生产证据和 commit hash 落盘前，`L-STAB`、`L-AUD-02` 保持 running，不得标记 closed。

## 六、审计限制

- 本轮未连接 QNAP、生产 PostgreSQL 或真实生产 SSH 网络。
- 未读取或提交未跟踪的数据库备份文件。
- 所有对抗测试仅使用本地临时 PostgreSQL、回环地址和文档占位凭据。

## 七、二次修复复验

针对本报告 R-01、R-02、R-04、R-05、R-06 已完成代码修复并复验：

- 新增统一可信身份解析：`message_read`、`message_inbox`、个人信箱 Resource 和
  `message_send` 均拒绝参数冒充；
- 新增跨 Agent inbox、Resource、send 拒绝矩阵，集成测试 39 项通过；
- 租约冲突路径改为单条 CTE 返回成功行或当前冲突行，不再二次读取；
- `ensure_messaging_schema()` 改为版本迁移就绪检查，未迁移数据库明确拒绝启动；
- `gate.sh` 导出 `PYTHONPATH=src`，使用 uv 自动创建隔离环境并安装 runtime/dev
  依赖；当前工作区与无 `.git` 的隔离树 full gate 均为 unit 45、integration 40、
  重放 23/23、brief 269 token 全绿。

复验结论：代码与本地验证闭环完成；R-03 的 QNAP 生产部署、备份对账和回滚演练
仍是唯一发布阻断，`L-STAB` 继续保持 running。
