# 会话状态归档 — 0.5.0 上线前审查 (2026-10-15)

> 本文档归档 DEV-2026-10-15-01 会话完整状态, 供跨会话接续。台账锚点 (跨会话通道):
> batch `DEV-2026-10-15-01` / task `L-AUD-03` (closed) / finding `F-REL-01`·`F-REL-02` (open) / DevLog `#113` (向量化)。

## 1. 会话定位

对未部署批次 DEV-2026-10-14-01 (0.5.0 多用户分组权限层 + 站内邮箱, 提交链 `7c7b010..2692efc`, 见 `docs/session_state_2026-10-14.md`) 执行上线前审查。

**结论: 代码面可上线。** 权限链经 PG 18.6 实证有效, 门禁本检出复跑 5/5 全绿; 存在 2 个发版收口项 (F-REL-01/02) 建议构建镜像前修掉。**未部署 (保持)。**

## 2. 提交链

| commit | 说明 |
|---|---|
| `4894857` | 会话起点 (上一会话终态: 2692efc 之上追加部署兼容性核验文档) |
| `83c6162` | 本会话唯一提交: `docs(devlog)` — DEVLOG.md 归档 L-AUD-03 + F-REL-01/02 + 批次章节 [DEV-2026-10-15-01]。已推送 origin/main |

无代码变更; 测试库实验与门禁均为只读性验证, 未产生工作区残留。

## 3. 审查执行记录

方法论: 廉价摸底 (DEVLOG/提交链/diff stat) → 逐层代码审查 → 疑点不做文档推断、直接隔离环境实证 → 门禁当前检出复跑。

**覆盖面**: sql/09·10 (幂等性/RLS 策略/SECURITY DEFINER search_path 锁定); db.py 双平面池、`SET ROLE lb_u_*` + `logbook.agent` GUC、自定义 reset 钩子、`_qi()` 标识符转义、63 字节哈希截断、授权链 (bootstrap/open grants/挂组收口/残留回收); mcp_server `_require_admin` 门禁、信箱 5 新工具 (全走 `_trusted_agent_identity`)、project_init 挂组与 PROJECT_NOT_VISIBLE; backfill 脚本 (opt-in + dry-run); negotiation/models 变更; 引导双入口覆盖 (stdio `run_stdio()` 与 SSH 子进程 `logbook.cli mcp` 同路径)。

**实证 ① (权限链有效性, 本审查关键项)**: 群组角色 `NOLOGIN NOINHERIT` + 数据面仅 `SET ROLE lb_u_*` 的组合, 在 PG 16+ (GRANT 增加 WITH INHERIT 选项) 存在"成员关系是否继承"疑点。于隔离 PG 18.6 (deploy/test/compose.yaml, 与生产同版本) 复刻角色拓扑实测: `pg_auth_members.inherit_option = True`, `SET ROLE 用户角色` 后群组 schema 授权直接可用 —— **文档记忆与实测不符, 继承生效, 权限链有效**。复作入口: PG 大版本升级后需重跑该探针 (建 NOINHERIT 组角色 + INHERIT 用户角色 + GRANT + SET ROLE 读写验证)。

**实证 ②**: `scripts/gate.sh full` 5/5 全绿 (unit / integration / 18 笔幂等重放零差异 / brief 276 token 达标 / dedup dry-run), 99 用例, 与 DEV-2026-10-14-01 记录一致。

## 4. 结论与发现

**发版前必修 (均一行级)**:
- **F-REL-01 (P2, open)**: 版本号未随批次提升 — `pyproject.toml`=0.4.2 / `src/logbook/__init__.py`=0.3.0 / CHANGELOG=0.5.0。`serverInfo.version` 自包元数据注入 (mcp_server.py:61), 不升版则生产终验版本辨识失败。修法: pyproject bump 0.5.0 并对齐 `__init__.py`。
- **F-REL-02 (P3, open)**: `deploy/qnap/compose.yaml.example` 与 `.env.example` 未文档化 `LOGBOOK_ADMIN_AGENTS` (部署清单 §6 第 2 步要求注入)。漏配影响有限: 仅群组管理面 ADMIN_REQUIRED。

**次要观察 (不阻塞、无需动作)**: 信箱 RLS UPDATE 策略允许收件人改写所收信件任意列 (MCP 层仅限定列更新, 无暴露路径); backfill 手动模式 schema 名只包裹未转义内嵌引号 (本地 admin 工具, 风险可忽略); devlogs/researches 无 DELETE 策略 = 非 owner 默认拒删, 与审计定位一致。

**生产兼容结论**: 维持 session_state_2026-10-14.md §8 判定 — 迁移纯增量 / owner 豁免 RLS / 存量 schema 引导补 legacy 全通授权, **部署不影响现有使用**。三项非破坏行为变化: brief 仅本人未读、每会话双池连接、未读 500 拒收上限。

## 5. 台账归档状态

| 载体 | 内容 |
|---|---|
| docs/DEVLOG.md (83c6162) | 任务台账 L-AUD-03 (closed, 26 条) + 发现台账 F-REL-01/02 (open, 8 条) + 批次章节 [DEV-2026-10-15-01] |
| logbook MCP batch | `DEV-2026-10-15-01` completed (含方法论笔记: PG 升级重跑继承探针等复作入口) |
| logbook MCP task | `L-AUD-03` closed, commit_hash=83c6162, type=verify |
| logbook MCP finding | `F-REL-01` P2 open / `F-REL-02` P3 open, reporter=zcode, source=audit |
| logbook MCP devlog | `#113` (向量索引已生成): PG16+ 继承语义实证 + 发版收口项定位 |

## 6. 会话内用户问答澄清 (决策记录)

1. **用户名策略**: 自研策略非延续 Linux (`^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`), 驱动因素为 PG 角色 63 字节标识符约束 / SSH authorized_keys 路径安全 / RLS 字符串等值比较; 允许大写与点、上限 64、允许纯数字, 是 Linux 惯例的放宽超集。同正则在 ssh_server·db.py·mcp_server·backfill 四文件五处复制, 建议将来收拢单一常量 (未立项)。
2. **授权语义**: 开户自动 (SSH 用户名 → LOGBOOK_AGENT_ID → 首连自动注册 principals + PG 角色), 授权手工 (admin 三步: group_record → group_member_add → group_grant); 与 Linux 用户组/宿主机权限零继承 (SSH 层为容器内 AsyncSSH 虚拟用户, 不查 /etc/passwd); 零配置 = 全部项目 legacy 全通, 行为同单用户时代。
3. **部署影响确认**: 不影响现有使用 (§8 维持); backfill 回填脚本是唯一改变行为面的动作, 保持 opt-in 先 --dry-run。

## 7. 接续指引 (下一步动作序列)

1. 修 F-REL-01: `pyproject.toml` version → 0.5.0, `src/logbook/__init__.py` `__version__` → 0.5.0;
2. 修 F-REL-02: `deploy/qnap/compose.yaml.example` + `deploy/qnap/.env.example` 补 `LOGBOOK_ADMIN_AGENTS` 行;
3. finding ×2 置 fixed 闭环 → 构建镜像 → 按 `docs/session_state_2026-10-14.md` §6/§8 部署清单上线 (pg_dump 冷备 + 对账基线先行 → compose 换 tag → up -d 自动迁移);
4. 部署后验证: §8 清单三项 (测试信回环 / brief 确认无 `ACL bootstrap degraded` / 按需配 admin) + `serverInfo.version=0.5.0` 终验 + 未读积压 SQL 确认。

## 8. 环境与通道备忘

- 隔离测试库: `scripts/test_db.sh` (compose, `127.0.0.1:15432/logbook_test`), 门禁自管启停, 本会话实验残留已清理、已随 trap 停止;
- 生产通道: 未触碰。QNAP 生产操作沿用既有通道 (见 session_state_2026-10-14.md §8 核验受限项: `ssh nas` 需交互密码);
- 回滚: 未部署故无需; 部署后回滚沿用 §6 (revert + 重建容器, RLS/grant 对 owner 透明)。
