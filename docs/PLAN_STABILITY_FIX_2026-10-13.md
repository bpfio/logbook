# Logbook 稳定性审计修复计划

| 项目 | 内容 |
|---|---|
| 计划日期 | 2026-10-13 |
| 关联审计 | `docs/AUDIT_LOGBOOK_STABILITY_2026-10-13.md` |
| 代码基线 | `149a646` |
| 关联任务 | `L-STAB` |
| 当前状态 | planned |
| 修复范围 | A-01 至 A-08；先建立可复现验证能力，再修复发布阻断、并发正确性、安全与 SSH 生命周期 |

## 一、目标与边界

### 1. 修复目标

1. 全新数据库、既有数据库和 QNAP 数据卷均能通过同一条版本化迁移链完成初始化。
2. 文件租约在数据库层严格互斥，连接池、Schema 首检和项目 DDL 初始化不发生并发竞态。
3. 信箱读取只允许真实收件人执行，错误身份不能读取正文，也不能改变已读状态。
4. SSH 不依赖固定 300ms 猜测任务结束，慢调用与 stdin EOF 不再导致响应取消。
5. 生产模式不存在默认弱口令；缺少可信公钥或强凭据时必须在监听端口前失败。
6. 干净 Git 检出无需开发者本地 PostgreSQL、本机 `.env` 或未跟踪部署文件即可复现完整门禁。

### 2. 非目标

- 不引入论坛、社交、评论、点赞或热度模型。
- 不增加常驻重依赖、数百线程或大内存池；生产进程目标仍为 100MB 级。
- 本轮不扩展新的业务实体，迁移状态表仅作为运维元数据。
- 未经迁移兼容性验证，不执行破坏性 Schema 变更，不直接热升级 QNAP。

## 二、实施原则

1. **验证能力优先**：A-08 的隔离数据库门禁先于功能修复落地，否则无法证明其余修复有效。
2. **数据库原生原子性**：租约、鉴权和迁移锁由 PostgreSQL 条件写入、行锁或 advisory lock 保证，不依赖 Python 协程先后顺序。
3. **协议生命周期驱动**：SSH/MCP 不解析业务 JSON-RPC 帧，不自造协议状态机；通过 stdin、channel 和子进程生命周期传递 EOF。
4. **显式信任边界**：工具参数中的 `agent_name` 不是身份凭证；身份必须来自已认证 SSH principal、受控 stdio 环境或等价可信上下文。
5. **依赖先调研后选型**：迁移 runner、临时数据库测试框架和 SSH 生命周期方案须先完成小规模选型探针，再锁定实现。
6. **每项修复独立可回滚**：优先 additive migration；任何非向后兼容变更必须同时提供回滚 SQL、备份恢复条件和旧镜像兼容性说明。

## 三、修复任务分解

### S0：建立可复现质量门禁（A-08，先决条件）

**主要文件**

- `scripts/gate.sh`
- `tests/test_deploy_secrets.py`
- `tests/conftest.py` 或等价测试夹具
- `pyproject.toml`
- `.github/workflows/*`（如仓库启用 CI）

**实施内容**

1. 完成迁移测试框架选型后采用 Compose test profile：仅在开发与门禁期启动隔离
   `pgvector` 容器，不增加生产依赖，也不要求宿主机预装 PostgreSQL。
2. 将测试分层为：
   - `unit`：不访问 PostgreSQL，可在最小环境直接运行；
   - `integration`：使用临时 PostgreSQL + pgvector，自动迁移、自动清理；
   - `replay`：仅在临时数据库执行 18 笔真实写入重放；
   - `production-smoke`：仅在显式目标环境执行。
3. `gate.sh` 增加前置检查：Python 依赖、Docker、镜像、端口、迁移状态和部署模板；失败时输出可操作诊断。
4. 部署测试只读取 Git 跟踪的 `compose.yaml.example`、`.env.example`，通过临时目录合成测试环境，不读取 `deploy/qnap/compose.yaml` 或 `.env`。
5. 重放、brief 度量和去重检查全部指向临时数据库，禁止意外写开发者或生产数据库。

**验收门**

- 新克隆仓库执行一条命令即可完成 unit + integration + replay。
- 未启动本机 PostgreSQL 时，门禁仍可通过。
- 缺少 Docker 时，门禁在第一阶段明确失败并说明原因。
- 部署测试不再依赖任何未跟踪文件。

### S1：版本化迁移链与全新部署（A-01，P0）

**主要文件**

- `sql/01_init.sql` 至 `sql/07_research_knowledge_base.sql`
- 新增迁移 runner、迁移清单与 `schema_migrations` 元数据
- `docker-compose.yml`
- `Dockerfile`
- `deploy/qnap/compose.yaml.example`
- `deploy/qnap/DEPLOY.md`

**选型探针**

1. 评估现有裸 SQL + 轻量 runner、Alembic、golang-migrate、Atlas 等方案。
2. 选择标准：支持版本号与 checksum、并发锁、失败事务、幂等重放、已有库识别；不得引入重量级生产运行态。
3. 若选择轻量 runner，必须复用现有 SQL 文件并集中实现锁、checksum 与状态表，避免每个入口各自维护迁移逻辑。

**实施内容**

1. 建立唯一入口，例如 `python -m logbook.migrate status|up|verify`。
2. 使用数据库 advisory lock 防止多个容器或 Agent 同时迁移。
3. 记录版本、文件 checksum、执行时间和结果；同一版本内容变化时拒绝静默覆盖。
4. 对全新库顺序执行 `01` 至当前版本。
5. 对无 `schema_migrations` 的既有库先执行结构探测，生成“已应用/缺失/冲突”报告；只补缺失版本，不盲目重放 `03_dedup` 等有状态操作。
6. 统一 root Compose、QNAP Compose 和应用镜像的迁移入口；删除 QNAP 仅创建 `shared` schema 的伪初始化路径。
7. 更新部署文档和备份/恢复说明。

**验收门**

- 空数据库从首个版本迁移到当前版本成功，连续执行第二次为 no-op。
- `task_upsert`、`project_init`、消息、租约、调研知识库在全新库全部可用。
- 既有数据库状态报告无冲突，补齐缺失版本后 schema checksum 一致。
- 迁移中途失败不会留下“部分版本已标记成功”的状态。
- QNAP Compose 不再依赖只含 `CREATE SCHEMA` 的 `deploy/qnap/init.sql`。

### S2：并发正确性与稳态性能（A-02、A-03、A-04）

#### S2.1 文件租约原子互斥（A-02）

1. 将 `acquire_file_lease()` 收敛为单条条件 UPSERT。
2. 冲突条件必须是“租约未过期且持有者不是当前 Agent”；同一 Agent 可续租，过期租约可被接管。
3. 使用 PostgreSQL 时钟判断过期，不使用应用侧 `now` 参与决策。
4. UPSERT 未返回行时统一返回 `LEASE_CONFLICT` 和冲突 Agent，不读取后再次写入。
5. 增加 2 Agent、3 Agent、过期接管、同 Agent 续租和 100 轮重复并发测试。

#### S2.2 连接池与 Schema 单飞初始化（A-03）

1. 为 `Database.connect()`、关闭和事件循环切换建立明确的异步同步原语。
2. 锁内二次检查 `_pool`，确保并发首调用只创建一个池；失败时清理半初始化状态。
3. 消息 Schema、调研 Schema 分别使用独立锁并采用 double-checked locking，只有 DDL 成功后才发布 ready 标志。
4. 关闭期间的并发连接调用必须失败或等待，不产生新池。

#### S2.3 项目 DDL 一次性初始化（A-04）

1. 每进程按项目缓存 Schema ready 状态，常规读写不再调用 `shared.init_project_schema()`。
2. 首次初始化使用项目级锁，避免同一项目的并发首请求重复执行 DDL。
3. Schema 升级只通过 S1 的版本化迁移执行；迁移成功后使进程缓存失效，必要时要求重启。
4. 保留显式 `schema verify` 或轻量存在性检查，但稳态查询不执行建表、建索引语句。

**验收门**

- 100 轮并发租约测试每轮恰好一个成功。
- 三个并发首连接只调用一次 `create_pool`，泄漏池引用为 0。
- 三个并发 Schema 首请求各只执行一次 DDL。
- 稳态任务读写不再执行 `shared.init_project_schema()`。
- 集成测试和真实写入重放全绿。

### S3：信箱鉴权与 SSH 凭据安全（A-06、A-07）

#### S3.1 收件人鉴权（A-06）

1. 定义可信调用身份：
   - SSH 模式从认证 principal 映射到允许的 Agent 别名；
   - stdio 模式从受控环境变量或启动配置读取 Agent ID；
   - 工具参数中的 `agent_name` 只能作为兼容别名，不能覆盖可信身份。
2. `read_agent_message()` 必须要求明确收件人，SQL 同时限定 `id = $1 AND to_agent = $2`。
3. 数据库更新后再记录 `read_by_agent`；非收件人不能改变 `is_read` 或 `read_at`。
4. 对外统一返回不可区分的 `MESSAGE_NOT_READABLE`，避免通过“不存在/无权”差异枚举 ID；内部日志区分两种原因。
5. 收件人重复读取保持幂等。

#### S3.2 SSH 凭据与监听配置（A-07）

1. 删除代码和 Compose 中所有 `logbook` 口令默认值。
2. 默认关闭口令认证；仅公钥认证时必须存在至少一个有效授权公钥。
3. 如显式开启口令认证，必须提供强口令，并拒绝已知默认值、空值和过短值。
4. 在 `asyncssh.create_server()` 之前完成配置校验；生产模式缺凭据时拒绝启动并输出修复指引。
5. 将默认监听地址收紧为 `127.0.0.1`；QNAP 显式配置局域网监听时必须同时声明防火墙要求。
6. Compose 示例不提供弱口令 fallback，部署文档改为公钥优先流程。

**验收门**

- 非收件 Agent 读取失败，正文不泄露，已读状态不变。
- 正确收件人首次读取成功，重复读取幂等。
- 未配置 SSH 凭据、仅配置默认口令或生产模式配置过弱时，服务不监听端口。
- 正确公钥可登录；错误公钥和错误口令稳定失败。
- 测试和文档不包含真实凭据。

### S4：SSH/MCP 生命周期修复（A-05）

**方案要求**

1. MCP 模式下，客户端 write-side EOF 不得立即关闭子进程 stdin。
2. 子进程 stdin 在 SSH session 存活期间保持打开，让在途 JSON-RPC 请求继续执行；session 结束或连接丢失时再关闭 stdin 并回收进程。
3. stdout/stderr 必须在终止子进程前完成有界 drain，避免响应尾部丢失。
4. CLI 模式如确需 stdin EOF，应单独处理，不复用 MCP 生命周期。
5. 不通过固定 300ms、不通过解析业务 payload 判断任务完成；仅解析 JSON-RPC
   envelope ID 以跟踪尚未回包的请求。

**测试夹具**

- 新增受控慢调用 MCP 测试服务，支持 1ms、500ms、5s 和异常退出。
- 使用真实 AsyncSSH client 打开 channel，发送请求后关闭 write side，但保持读取响应。
- 覆盖响应完整回写、连接中断、子进程退出、超时清理和无孤儿进程。

**验收门**

- stdin EOF 后超过 5s 的调用仍完整收到 JSON-RPC 响应。
- 调用完成后再关闭 session，不残留子进程。
- 连接异常时有界超时清理，stdout 尾部不截断。
- 不再存在固定 `sleep(0.3)` 决定协议生命周期的路径；读取到客户端 stdin EOF
  后，只有 envelope 中所有待回请求均已有结果或错误，才关闭子进程 stdin。

### S5：集成验收与 QNAP 发布

1. 执行 clean-checkout 门禁和全部并发测试。
2. 在临时 PostgreSQL 上完成全新安装、重复迁移、升级和回滚演练。
3. 升级前导出数据库备份，记录大小与 checksum；备份不得进入 Git。
4. 构建并保留旧、新两个镜像标签，先在隔离副本执行 25 个工具、8 个 Resources、4 个 Prompts 冒烟。
5. QNAP 热升级后执行数据量对账、信箱越权、租约并发、SSH 慢调用和凭据失败测试。
6. 观察 PostgreSQL 连接数、锁等待、进程 RSS、线程数和 EOF 后响应时延。
7. 回滚只允许使用已验证的旧镜像和向后兼容 Schema；非兼容迁移必须从备份恢复。

**发布阻断条件**

- 任一 P0/P1 验收项失败。
- 干净数据库迁移链无法重复执行。
- 迁移前后数据量无法对账。
- 生产 SSH 仍接受默认或缺失凭据。
- 进程内存或线程数突破 2C2G/NAS 资源底线。

## 四、测试矩阵

| 修复项 | 单元测试 | 数据库集成测试 | 并发测试 | 端到端测试 |
|---|---|---|---|---|
| A-01 | manifest/checksum/状态机 | 空库迁移、重复执行、既有库补迁移 | 多实例迁移锁 | 新卷 MCP 冒烟 |
| A-02 | 返回值与错误映射 | 条件 UPSERT | 2/3 Agent、100 轮 | 跨进程租约 |
| A-03 | fake pool 单飞 | Schema ready 发布 | 首连接、DDL 首检 | 多工具并发启动 |
| A-04 | ready 缓存命中 | 稳态查询无 DDL | 同项目并发首查 | 任务台账全链路 |
| A-05 | 生命周期状态 | 不适用 | 慢调用、EOF、断连 | 真实 AsyncSSH MCP |
| A-06 | 身份映射与错误包装 | SQL 收件人限定 | 并发读取 | 跨 Agent 拒绝 |
| A-07 | 启动配置校验 | 不适用 | 多次启动 | 真实公钥/口令认证 |
| A-08 | preflight 分支 | 临时库 fixture | 重放隔离 | clean checkout |

## 五、建议执行顺序与依赖

1. **S0/A-08**：先建立隔离门禁，是其他任务的验收基础设施。
2. **S1/A-01**：解决 P0 迁移链，是所有数据库测试和生产恢复的前提。
3. **S2/A-02 → A-03 → A-04**：先保证原子互斥，再收敛连接池，最后移除稳态 DDL。
4. **S3/A-06、A-07**：在任何新卷或 SSH 对外开放前完成安全阻断。
5. **S4/A-05**：依赖 S0 的 SSH 端到端夹具，可与 S2/S3 并行开发。
6. **S5**：所有 P0/P1 关闭后才执行 QNAP 热升级与回滚演练。

## 六、完成定义

本轮修复只有同时满足以下条件才可关闭 `L-STAB`：

- A-01 至 A-08 均有代码修复、回归测试和可引用证据。
- 全量 unit + integration + replay 在干净环境全绿，不再依赖本机 PostgreSQL。
- 空数据库连续两次迁移成功，既有数据库迁移状态报告无冲突。
- 租约 100 轮测试每轮唯一成功；连接池和 DDL 首检并发探针各只执行一次。
- 非收件人无法读取或改变信件状态。
- 慢于 5s 的 MCP 调用在 stdin EOF 条件下完整收到响应。
- 生产 SSH 缺少可信凭据时拒绝启动。
- QNAP 升级前备份、升级后对账和旧镜像回滚演练通过。
- `docs/DEVLOG.md` 附真实 commit hash 与验证报告指针后，任务才能进入 `closed`。

## 七、残余风险与待调研事项

1. 迁移 runner 已收敛为复用现有 `sql/` 的轻量版本链，并以 checksum、状态表和
   advisory lock 约束；残余风险转为真实 QNAP 数据卷的采用报告与回滚验证。
2. 版本锁定的 AsyncSSH 2.24.1 真实客户端已通过 5 秒慢调用、stdin EOF 和子进程
   回收测试；生产网络、断连风暴与资源指标仍须在 S5 验证。
3. SSH principal 到可信 `LOGBOOK_AGENT_ID` 的传播已实现；既有非 SSH 客户端的
   身份兼容迁移仍需在生产冒烟中核验。
4. 既有 QNAP 数据卷尚未执行本轮迁移升级、备份对账和旧镜像回滚演练。
5. 未跟踪的数据库备份文件仍需由数据所有者决定存放、权限、保留期和脱敏策略；
   本轮未读取其内容，也不将其纳入 Git。

## 八、本地执行状态

- S0~S4 代码修复已完成；`./scripts/gate.sh full` 本地全绿。
- unit 44 项、integration 38 项通过；临时 PostgreSQL 迁移、幂等重放、brief
  度量和只读去重检查均通过。
- 文件租约并发已按完成定义提升至 100 轮，每轮唯一成功者。
- 真实 AsyncSSH 2.24.1 端到端测试证明 5.2 秒调用在客户端 stdin EOF 后仍完整
  回写响应，子进程随后正常退出且无残留。
- S5 的 QNAP 升级前备份、镜像标签、数据对账、资源观测和回滚演练尚未执行；
  在这些证据和 commit hash 落盘前，`L-STAB` 保持 `running`。
