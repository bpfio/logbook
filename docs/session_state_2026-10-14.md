# 多用户权限层 + 站内邮箱完成面会话状态（2026-10-14）

## 1. 会话定位

- 基线：`main@22e7ed4` 之上的已完成开发批次。
- 关联任务：`MU-1`（权限层, closed）、`MU-2`（站内邮箱, closed）、批次 `DEV-2026-10-14-01`（completed）。
- 权威文档：
  - 设计决策：logbook 台账 `research[11]`（PG 原生 role/GRANT/RLS 选型与对抗成本）
  - 关键排障：logbook 台账 `devlog[99]`（asyncpg 池归还 RESET ALL 清身份 GUC）
  - 任务台账：`docs/DEVLOG.md` → 章节 [DEV-2026-10-14-01]
  - 变更记录：`CHANGELOG.md` → [0.5.0]（未部署）
- 当前裁决：**开发与验收闭环，未部署（用户指示）**。生产上线为独立批次，前置清单见 §6。

## 2. 提交链

| commit | 内容 |
|---|---|
| `7c7b010` | feat(multi-user): 权限层 + 邮箱完成面（11 文件，+1761/-25） |
| `251ac93` | docs(state): DEVLOG 台账/批次章节 + CHANGELOG 0.5.0 |
| `e23a5fe` | fix(test): 门禁库名动态化（gate full 兼容） |
| (本提交) | docs(state): 会话状态归档 |

## 3. 设计决策（语义方案 A，用户确认）

- **可见性语义**：组内互见互写，跨组不可见；未挂组项目保持 legacy 全通（存量零破坏）；admin（`principals.is_admin`）全见。
- **执行面选择**：PG 原生 role/GRANT/RLS 硬隔离，不自造应用层 ACL（防逐路径遗漏）；应用层只管成员关系与语义化报错。
- **双平面连接池**：数据面 `init` 钩子 `SET ROLE lb_u_<principal>` + `set_config('logbook.agent', ...)`；系统面（登录角色）承载 DDL/授权/引导；内存仍单进程两池（min1/max3 ×2），符合 100MB 级铁律。
- **挂载即收口**：项目挂组时收回 `logbook_authenticated` 公共授权、改授群组角色（含 default privileges）；摘除最后挂载组自动回到 legacy 开放态。
- **信箱可见性与群组正交**：`shared.agent_messages` 行级 RLS 仅收发双方；admin 也不开正文（审计走 `read_by_agent` 元数据）。
- **砍掉项**：SMTP/IMAP 外发收信、附件（含指针附件，未来再说）、CC/密送/群发 —— 全部违反轻量铁律或无场景。
- **通道策略**：多用户远程接入只走 SSH（公钥认证）；stdio 限定本机可信进程。

## 4. 交付清单

**批次 A（权限层, MU-1）**

- `sql/09_multi_user_acl.sql`：四表 + `acl_current_agent/acl_is_admin/acl_project_visible/acl_principal_exists`（SECURITY DEFINER, search_path 锁定）+ shared.projects/devlogs/researches/file_leases RLS（owner 豁免）。
- `src/logbook/db.py`：`identity`/`_system_pool` 双平面、`pg_role_name`（63 字节确定性角色名）、`ensure_acl_bootstrap`（logbook_authenticated 角色 + shared 面授权 + 默认权限 + 存量 schema 开放授权）、`ensure_principal`（账户行 + PG 角色 + 群组角色 GRANT 同步）、群组 CRUD/挂载/成员、`probe_project`、`reset_data_pool`（授权变更后池重建刷新 SET ROLE 快照）。
- `src/logbook/mcp_server.py`：`_require_admin` 门禁、`_session_bootstrap`（run_stdio 引导，失败降级 legacy）、群组管理 7 工具、`project_init` 挂组与 `PROJECT_NOT_VISIBLE` 语义化拒绝。
- `scripts/backfill_permissions.py`：存量项目发现式回填（`--discover` 自动收集参与账户建 `<schema>-team` 组挂载；`--dry-run` 预览）。
- `src/logbook/negotiation.py`：越权提示补充 PG 硬边界语义。

**批次 B（站内邮箱, MU-2）**

- `sql/10_mailbox_completion.sql`：`is_archived/archived_at` 列、`acl_mailbox_has_room`（GUC `logbook.mailbox_cap` 缺省 500）、agent_messages 点对点 RLS（SELECT/INSERT/UPDATE 三策略，UPDATE 放宽到收发双方以支持 thread 物化）、trgm 检索索引。
- `src/logbook/db.py`：`reply_agent_message`（对方自动推导 + thread_id 物化）、`get_agent_thread/get_agent_sent/search_agent_messages/archive_agent_message/count_agent_unread`、收件余量前置校验、inbox 归档过滤。
- `src/logbook/mcp_server.py`：`message_reply/thread/sent/search/archive` 5 工具；`message_send` 收件人未注册告警（`recipient_registered=false` + warning）；`logbook_brief` 本人未读角标（`my_unread`）。
- `src/logbook/models.py`：AgentMessage 增 `is_archived/archived_at`。

## 5. 关键排障记录

1. **asyncpg 池归还 RESET ALL 清身份 GUC**（devlog[99]）：init 钩子只在建连时执行，归还默认 reset 清掉 `logbook.agent`（角色保留），RLS 静默失效且仅在"同连接二次 acquire"场景暴露。修复：`create_pool(reset=self._reset_data_conn)` 归还时重新断言身份。教训：池化会话变量必须配对自定义 reset。
2. **测试工作区探测干扰**：测试进程跑在 `bpfio/logbook` 检出内，MCP 工具的跨项目写守卫拦截探针项目 → 测试用 `LOGBOOK_PROJECT` env 显式声明工作区。
3. **成员关系表不对数据面授权**：`principal_groups`/`groups` 刻意不授 SELECT（防成员关系泄漏），`get_principal_groups/list_groups` 走系统面读取。
4. **门禁库名硬编码**：容量测试 `ALTER DATABASE logbook` 写死库名，gate 的干净临时库名不同导致 setup 全 ERROR → 改 `current_database()` 动态获取（e23a5fe）。

## 6. 验收证据与上线前置

**验收（本会话）**

- 正式门禁：`scripts/gate.sh full` **5/5 全绿**（unit / integration / 18 笔幂等重放 / brief token 度量 / dedup dry-run，干净临时库）；
- 新增 `tests/test_permissions_mailbox.py` 14 用例：权限矩阵、加组显隐/移组隐没、摘组回开、devlog 密级、admin 全见、管理面门禁、信箱点对点隐私、回复/会话/归档、容量拒收、未注册收件人告警；
- 冒烟：stdio `_session_bootstrap`（身份引导 + admin 识别）+ 回填脚本 `--discover --dry-run` 发现逻辑均通过。

**上线前置（未做，用户指示不部署）**

1. 生产库 `python -m logbook.migrate up` 应用 09/10（RLS 对 owner 透明，旧容器读回无影响）；
2. compose 注入 `LOGBOOK_ADMIN_AGENTS`（如 `agy`）声明管理员；
3. 评估 `scripts/backfill_permissions.py --discover`（先 `--dry-run`）：执行后存量项目按历史参与账户挂组收口；不执行则存量保持开放态，随时可逐项目 `group_grant` 收口；
4. stdio 通道限本机；远程只走 SSH 且禁用口令认证（沿用 A-07 红线）；
5. 灰度建议：先单项目试点 `group_grant`，验证跨组账号确实不可见后再全面挂组。

**回滚**：`git revert` 提交链 + 重建容器即可；RLS/grant 对表 owner 透明，旧版本代码可读回全部数据；新增 PG 角色（`lb_u_*`/`lb_g_*`/`logbook_authenticated`）残留无害，可 `DROP ROLE` 清理。

## 7. 接续指引

- 权限裁决入口：`shared.acl_project_visible(schema, agent)`；数据面会话身份：`db._init_data_conn` / `_reset_data_conn`。
- 群组运营：MCP `group_record → group_member_add → group_grant` 三步；查询 `group_query`；自查 `principal_query`。
- 未做事项备忘：附件（若未来做，只做指针附件）、SMTP 桥（走外部 mail 通道）、stdio 多用户（不做）。
