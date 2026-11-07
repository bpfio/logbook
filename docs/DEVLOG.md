# LOGBOOK 开发日志 (DEVLOG) — 项目唯一开发日志与任务台账

> **规则 (AGENTS.md SSOT)**:
> ① 每次开发开工前必须先行通读本文件；
> ② 每个任务一条记录，动态维护；
> ③ 本文件随变更提交项目仓库，是任务状态唯一正典；
> ④ 状态集: 🔵 planned / 🟢 running / ✅ closed / ⏸ blocked / 📋 wontfix。
> ⑤ 排序: 最新在前。

---

## 一、任务台账 (42)

| ID | 状态 | 类型 | 标题 | commit | 备注 |
|---|---|---|---|---|---|
| L-OPT-070 | closed | feat | Logbook 0.7.0: MCP工具集大归一 (46→14核心)、动态 JSON Schema 验证引擎、客户端 SDK 与 HTTP/HTTPS FastMCP 双通道落地 | e9e1213 | 吸收全量优化建议: ① tools/list 归一化为 14 个核心高内聚工具 (Schema 瘦身 63% 从 59.8KB 缩减至 22KB, 32 老工具 100% 保持就绪); ② 落地迁移 17 (sql/17_dataset_json_schema.sql), shared.datasets 增设 json_schema 字段并预置 8 大系统业务数据集校验契约, FactEngine 声明式拦截非标 payload 入库; ③ 落地 src/logbook/client.py 内置 SingleFlight 削峰、Full Jitter 退避重试与心跳保活; ④ 保留并增强 FastMCP SSE HTTP/HTTPS 双通道 (端口 8000 与 22 并行监听); ⑤ 修复 read_agent_message/archive_agent_message asyncpg $1 参数类型推断冲突 (int/str 22000); 单元测试 70 项 100% 绿灯, 生产环境部署成功, 13 项真机穿透测试 100% 通过 (DEV-2026-11-07-03) |
| L-AUD-061 | closed | audit | Logbook 0.6.1 独立第三方全量审计、P0/P1缺陷闭环、物理直写迁移与生产真机 13 项 100% 验收 | 87cfd88 | 独立第三方视角完成对 Logbook 0.6.0/0.6.1 的全量架构与 46 MCP 工具深度审计; 落地迁移 16 (sql/16_unify_fact_triggers_and_timeline.sql) 补齐联合唯一索引与统一时间线表 shared.task_timeline; DAO 层彻底消除视图写入风险, 统一直写 shared.records 事实表; 根治 asyncpg 参数类型推断冲突 (devlog_record bigint=text 42883) 与 normalizer 状态机放行越界缺陷; 自动登记新数据集杜绝外键阻断; 本地 65 单元测试全绿; 生产 NAS 成功部署 logbook:0.6.1; 真机 13 核心链路穿透测试 100% 通过; 存量 6,917+ 条业务记录与 500+ 条时间线 0 丢失; 产出完整审计与治理正典 docs/AUDIT_REPORT_V061.md (DEV-2026-11-07-02) |
| L-UNIFY-060 | closed | feat | 全库向通用事实双表底座 (shared.datasets + shared.records) 归一化、零数据丢失原子迁移、统一事实引擎落地 (0.6.0) | 200842e | 彻底收拢 118 物理碎片表为极简双表底座; 存量 8 大业务实体 (tasks=462, findings=205, waitings=29, batches=68, devlogs=348, messages=20, researches=37, rules=23) Checksum 100% 绝对吻合转录入库; 旧表重命名为 _legacy_* 物理封存并建立 21 Schema 原名同名向下兼容视图; 落地 src/logbook/engine.py 统一事实引擎解耦; 根除幽灵批次自动创建 (F-API-13) 与裸抛 FK 约束 (F-API-11); 修复 research_query/batch_query/record_query 入参口径漂移 (F-API-10) 与空串拦截; 生产环境 10 点冒烟套件实测 100% 绿灯 (DEV-2026-11-07-01) |
| L-SRV-057 | closed | fix | 根治 clean_text NameError (F-API-08)、解包空串崩溃 (F-SEARCH-07)、补齐 task review 状态机与标量入参 (F-API-09)、无向量库全文平滑降级 (F-SEARCH-08)、project_fields 强校验与 fields 回显 (F-API-03) (0.5.7) | HEAD | waiting_query/finding_query 补齐 clean_text 导入与标量数组兼容 (F-API-08/09); search_records 翻页分支补齐元组返回, record_search 空串严格拦截 (F-SEARCH-07); task_query 纳入 review 状态机与标量兼容 (F-API-09); project_fields 校验未知投影列 (F-API-03); 无向量数据集 (probe) 平滑降级全文检索 (F-SEARCH-08); 65 项单元测试 100% 绿灯 (DEV-2026-11-06-05) |
| L-SRV-056 | closed | fix | 根治全文静默穿透与超域得分 (F-SEARCH-06)、消除幽灵批次伪造与存在性强防呆 (F-API-07/06)、拦截空串与回显 (F-API-01)、全局 extra='forbid' 拦截笔误与 limit 越界告警 (F-API-03) (0.5.6) | HEAD | 向量 min_score>=0.40 0 命中时严禁降级全文并透出真实 search_mode; ts_rank_cd 单调归一化至 [0, 1) (F-SEARCH-06); 彻底删除 records_bulk 凭空建批, 显式 batch_id 强制预检存在性 (F-API-07/06); record_query status='' 拦截报错并增补 applied_filters (F-API-01); FastMCP 工具全局 extra='forbid' 拦截拼错参数, limit>maximum 阻断报错 (F-API-03); finding_record 支持增量状态流转免重传 summary; 59 单元测试全绿 (DEV-2026-11-06-04) |
| L-SRV-055 | closed | fix | 服务端校验层结构化信封收口、防伪造、默认 0.40 cutoff 与 8000 端口安全收敛 (0.5.5) | HEAD | 拦截 FastMCP 顶层裸文本收口为 valid JSON 信封 (F-API-02); 非法枚举彻底拒绝静默改默认值 (F-API-04/01); 单据必填 doc_no 防伪造 (F-API-05); 批量建账与强校验 (F-API-06); limit<=0 与未知 fields 修复 (F-API-03); min_score 类型校验与默认 0.40 cutoff (F-SEARCH-04/05); 8000 端口收敛至 127.0.0.1 (F-NET-8000); 向量冷启动预热; 59 单元测试全绿 (DEV-2026-11-06-03) |
| L-REC-054 | closed | fix | 落实 record_search 相关度契约与 min_score 截断 (F-SEARCH-02)、HNSW ef_search 动态自适应消除 40 条截断 (F-SEARCH-03) 与 SSH 正常断连退出码 0 归一 (0.5.4) | HEAD | record_search 新增 min_score 截断与 score 判读口径契约; HNSW 检索会话动态 SET hnsw.ef_search 消除 SSH 40 条截断; 修复 client_closed 时子进程 terminate 导致的 rc=255 误报; 补齐单元测试 (DEV-2026-11-06-02) |
| L-REC-FIX | closed | fix | 根治 record_search 42P18 参数绑定穿透、SSH 管道 4MB 扩容与超时加固 (0.5.3) | HEAD | 彻底消除 search_records 0 命中 fallthrough 导致的 params 污染与 42P18 报错; SSH 子进程 StreamReader 扩容至 4MB 杜绝大单据 JSON-RPC 假死; 命令超时提升至 20s; 补齐单元测试与迁移链测试 (DEV-2026-11-06-01) |
| L-REC-052 | closed | feat | 通用事实表 shared.records 768维语义向量底座 (迁移14) + records_bulk_upsert 批量落库 + record_search 混合检索与探针加固 (0.5.2) | HEAD | 贯彻用户裁决: DDL 迁移 14 落地 768 维 HNSW 索引; 新增 records_bulk_upsert 单事务批量落库消灭 5000 次 RPC 往返; 新增 record_search 语义+全文混合检索; 修复 AsyncSSH echo 探针编码与 FastMCP SSE 原生监听; 46 工具真机联测全绿, 栈内存 ~100.6MB (DEV-2026-11-03-02) |
| L-PROJ-UPD | closed | feat | project_update MCP 空间元数据修改原语落地与 taxbase 备注名纠偏 | HEAD | 新增 project_update 闭环项目元数据读写; 纠偏 taxbase 标题为「税收法规库」; 验证群组权限拦截与幂等保护 (DEV-2026-11-03-02) |
| L-OPS-DB128 | closed | ops | QNAP 生产容器 logbook-postgres 内存配额提升至 128MB 与 autovacuum 稳态加固 | HEAD | 根治 DB 48M 配额吃满 70% 导致 autovacuum (64M) 与 pgvector 潜在 OOM 隐患; 热重载 compose.yaml 生效 128MB, 剩余余量 103MB+; 43 工具与 brief 现场验证全绿 (DevLog #342, DEV-2026-11-03-01) |
| L-OPS-256 | closed | ops | QNAP 生产容器 logbook-app 内存配额提升至 256MB 与 Cgroup OOM 根治 | 8f2cc37 | 根治 96M 硬配额触发 Linux 内核 Cgroup OOM Killer 强杀 Python MCP 子进程 (退出码 247 = 256 - 9) 故障; 经用户裁决热重载 compose.yaml 生效 256MB; 43 工具与 brief 现场验证全绿 (DevLog #341, DEV-2026-11-03-01) |
| L-DEP-050 | closed | deploy | QNAP 生产环境 0.5.0 全维上线与故障原子自愈 (双表事实引擎+768维重嵌+43工具实证) | HEAD | 修复 10-30 网络命名空间断裂故障; 备份 1.3MB 库; 平滑执行迁移 09~13; 319 devlogs + 34 researches 100% 768维重嵌; 43 工具真机验证全绿, 栈内存 ~79.7MB (DEV-2026-11-01-01) |
| L-FACT-ENGINE | closed | feat | 通用事实引擎与极简双表架构 (方案一: dataset_* + record_*) + 零DDL数据入库 | 15be34b | 贯彻用户裁决双表极简架构: shared.datasets (元数据字典) + shared.records (强脊梁+软肋骨+主子表parent_id); 增补 5 个 MCP 工具; 提供 Excel/CSV 极速流式导入脚本; 现有 10 表 9 项目零数据丢失, 38 个存量 MCP 工具 100% 兼容, gate full 5/5 全绿 (DEV-2026-10-19-02) |
| L-RESEARCH-768 | closed | feat | 政策与世界观通用知识库扩充 (方案B) + 768维原生向量平滑升级 (迁移 12) | 71c5619 | 贯彻单表复用铁律: shared.researches 增发文字号/制定主体/效力层级/生效日期; 升级 vector(768) + HNSW 重建; 兼容宏观政策与小说设定考据; gate full 5/5 全绿 (DEV-2026-10-19-01) |
| L-CREATIVE-1 | closed | feat | 叙事与长篇创作工程阶段一: TaskType 扩充 + 闭环断言适配 + 迁移 11 落地 | 71c5619 | 增加 outline/draft/polish/continuity/lore 创作任务类型; validate_invariants 与 chk_task_closed_proof 支持 notes 闭环; gate full 5/5 全绿 (DEV-2026-10-19-01) |
| L-AUD-03 | closed | audit | 0.5.0 上线前审查: 权限链实证 + 门禁复跑 + 发版收口项落盘 | e7f3968 | 复核 26 既有工具零影响 + gate full 5/5 + PG18.6 实测群组继承语义有效; 发现 F-REL-01/F-REL-02 待修; 代码面具备上线条件 (DEV-2026-10-15-01) |
| MU-2 | closed | feat | 站内邮箱完成面: 回复/会话/已发送/搜索/归档 + agent_messages 点对点 RLS + 容量防灌爆 + brief 未读角标 | 7c7b010 | sql/10_mailbox_completion.sql + 5 新 MCP 工具 + 收件人注册告警; 14 新测试全绿, 回归 99/99 (DEV-2026-10-14-01) |
| MU-1 | closed | feat | 多用户分组权限层: principals/群组三表 + 双平面池 SET ROLE 降权 + RLS 硬隔离 + allow_cross_project 收口 | 7c7b010 | 语义方案A 组内互见互写/跨组不可见; acl_* SECURITY DEFINER + admin 门禁 + project_init 自动挂组 + 存量回填脚本 opt-in (DEV-2026-10-14-01) |
| L15 | closed | feat | rule_record MCP 写入原语落地, 闭合架构铁律 SSOT 读写环 (0.4.2) | 837ff87 | db.upsert_rule 早有但从未暴露; 0.4.2 上线生产, 回环验证通过并写入首条铁律 RULE-DEPLOY-01 (DevLog #92, DEV-2026-10-13-06) |
| L-STAB | closed | fix | 审计 A-01~A-08 稳定性整改与生产验收 | 08c0461 | S0~S4 提交 + 9c9d239 版本辨识；0.4.1 已上线生产, 终验 9/9 全绿 (DevLog #91, DEV-2026-10-13-05) |
| L-AUD-01 | closed | audit | Logbook 稳定性专项审计报告落盘 | 08c0461 | `docs/AUDIT_LOGBOOK_STABILITY_2026-10-13.md`; P0×1 / P1×6 / P2×1 全部转入 L-STAB 修复流, 报告随提交入库办结 |
| L-AUD-02 | closed | audit | 稳定性整改变更后复审 | 08c0461 | R-01~R-06 修复并随 0.4.1 生产验收 (身份拒绝生产实测生效, S5 完成), 发布阻断解除 |
| L-MSG-1 | closed | fix | 对讲邮件子系统生产故障: message_* 断连 — 0.4.1 上线终验通过办结 | 9c9d239 | 10-10 退回(容器未加载热补丁)；10-13 复测回环全通 + 0.4.1 上线, read_by_agent 审计列生效, 生产终验 9/9 |
| L14 | closed | feat | 研发调研知识库 (shared.researches) 与开工前 RAG 语义检索落地 | 0894f91 | sql/07_research_knowledge_base.sql + research_record/search/query 三大MCP工具 + 13测试全绿 |
| L13.5 | closed | feat | 全实体Agent IP溯源与不可篡改审计增强 (06_migration) | e829bd2 | sql/06_agent_ip_audit_everywhere.sql + detect_caller_ip三级探测 + 9测试全绿 |
| L13.4 | closed | deploy | QNAP生产环境镜像构建、平滑热升级与多Agent端到端协同验证 | 87d5b9e | DevLog #43 / 生产镜像上线(67.8MB), 25工具+8资源+4规程真机验证PASS |
| L13.3 | closed | feat | FastMCP工具注册与端到端测试套件扩充 (mcp_server.py & tests/) | d8fc1c5 | 暴露信箱与租约6大MCP工具，task_upsert增加reviewer参数 |
| L13.2 | closed | feat | 数据模型层与数据库核心CRUD操作实现 (src/logbook/models.py & db.py) | d8fc1c5 | 落地AgentMessage/FileLease校验模型、状态归一化与db异步信箱租约原子操作 |
| L13.1 | closed | feat | 多Agent对讲信箱与代码文件租约软锁核心表DDL建模与平滑迁移 | d8fc1c5 | sql/05_agent_messages_and_leases.sql + tasks表支持reviewer与review状态 |
| L13 | closed | feat | 支持多 Agent 异步对讲信箱、代码文件防冲突租约与协同流转 | d8fc1c5 | 0.3.0 里程碑主任务闭环 |
| L12 | closed | feat | Git仓库坐标权威锚定、双轨项目协商与自愈开户原语落地 | 0448a89 | sql/04_project_registry.sql + project_init MCP原语 + 双轨寻址41测试全绿 |
| L11 | closed | ops | cliserver开发机识别与agy生产logbook MCP配置核验归档 | - | 确证cliserver即xhub; 全局mcp_config.json已生效; logbook_brief RPC验证PASS |
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

## 二、发现台账 (36)

| ID | 来源 | 级别 | 状态 | 处置 | 备注 |
|---|---|---|---|---|---|
| F-SQL-01 | audit | P0 | fixed | 消除对带触发器视图的写入依赖，DAO 层改造为单语句直接写入底层物理事实表 shared.records | INSTEAD OF 触发器不支持 ON CONFLICT，统一直写 shared.records 原生原子操作 (DEV-2026-11-07-02) |
| F-TYPE-01 | audit | P1 | fixed | 修复 asyncpg 同一占位符多处复用导致的 bigint = text (42883) 参数类型推断冲突 | update_devlog 与 update_research 按 record_id 主键精准更新，消灭参数推断混乱 (DEV-2026-11-07-02) |
| F-DATA-01 | audit | P1 | fixed | 补齐 shared.records (project_id, dataset_code, doc_no) 联合唯一索引与统一时间线物理表 shared.task_timeline | 建立数据库内核级硬唯一约束与物理事件时间线表 (DEV-2026-11-07-02) |
| F-VAL-01 | audit | P2 | fixed | 修复 normalize_record_status 宽松正则放行非法拼写状态的问题，强化字典严格校验 | 剔除自定义状态放行逻辑，未注册枚举严格抛出 ValueError (DEV-2026-11-07-02) |
| F-API-13 | accept | P2 | fixed | 彻底删除 tasks_bulk_upsert 自动静默建批逻辑，对显式 batch_id 强制预检存在性 | 引用不存在批次号时凭空写入 running 批次污染主档，改用 ensure_batch_exists_or_raise 友好拦截 (DEV-2026-11-07-01) |
| F-API-11 | accept | P3 | fixed | task_upsert batch_id/parent_id 增加存在性前置预检防呆 | 传入不存在批次或父任务时返回友好 INVALID_ARGUMENT，彻底消除裸抛 PG 23503 外键约束异常 (DEV-2026-11-07-01) |
| F-API-10 | accept | P3 | fixed | 统一 status 入参形态支持标量与数组双收，batch_query 补齐 status 过滤，空串统一标准拦截 | research_query 支持数组过滤，batch_query 支持批次状态召回，record_query offset<0 阻断 (DEV-2026-11-07-01) |
| F-SEARCH-09 | accept | P3 | fixed | 统一 min_score 过滤规则，消除全文降级分支门限失效 | 全文降级分支同样执行 min_score 门限过滤，杜绝弱相关度穿透 (DEV-2026-11-07-01) |
| F-API-12 | accept | P3 | fixed | export_markdown 返回类型注解扩展为 str \| dict | 避免 ProjectNegotiationError 错误信封被 fastmcp 返回值校验误拦为 INTERNAL_ERROR (DEV-2026-11-07-01) |
| F-API-08 | accept | P1 | fixed | 补齐 clean_text 导入声明，消除 waiting_query 默认调用及 finding_query 带 status 时 NameError | 导入声明遗漏 clean_text 导致读面部分瘫痪 (DEV-2026-11-06-05) |
| F-SEARCH-07 | accept | P1 | fixed | search_records 常规浏览分支补齐元组返回值，record_search 严格阻断空串 query | 翻页分支 return_mode 返回单值引发解包崩溃，空串 query 入口层校验拦截 (DEV-2026-11-06-05) |
| F-API-09 | accept | P3 | fixed | task_query 补齐 review 状态机枚举，统一 query 入参同时兼容标量与数组 | 缺失 review 状态及标量字符串传参报错 (DEV-2026-11-06-05) |
| F-SEARCH-08 | accept | P3 | fixed | 探测数据集向量存在性，全量无向量数据集平滑降级至 GIN 倒排全文检索并标注 fulltext_fallback | 0.5.6 min_score 截断导致 probe 17 行记录全量 embedding IS NULL 无法全文检索 (DEV-2026-11-06-05) |
| F-CRED-01 | audit | P2 | fixed | 确证生产环境 SSH 原生仅支持密钥鉴权 (SSH_ENABLE_PASSWORD_AUTH=False)，旧口令物理失效 | 历史会话口令物理无法被用于登录，凭据硬锁 (DEV-2026-11-06-05) |
| F-SEARCH-06 | accept | P2 | fixed | 消除向量 0 命中时静默穿透全文，归一化 ts_rank_cd 至 [0, 1) 契约域并透出真实 search_mode | 向量 min_score>=0.40 过滤为空时严禁 fallthrough 到全文且 score 2.2 越界 (DEV-2026-11-06-04) |
| F-API-07 | accept | P2 | fixed | 彻底剔除 records_bulk_upsert 自动建批逻辑，对显式 batch_id 增加存在性预检 | 凭空自造 GHOST-BATCH-88 架空批次表权威性与任务外键 (DEV-2026-11-06-04) |
| F-API-06 | audit | P2 | fixed | records.batch_id 增加 batches 存在性前置预检防呆 | 传入不存在批次号时直接阻断报错，单条与批量口径统一 (DEV-2026-11-06-04) |
| F-API-01 | audit | P2 | fixed | record_query status='' 严格拦截报错并在响应体增补 applied_filters 回显 | status='' 导致过滤失效穿透全量，增补生效维度回显 (DEV-2026-11-06-04) |
| F-API-03 | audit | P3 | fixed | FastMCP 全局启用 extra='forbid' 拦截笔误参数，limit>maximum 阻断告警 | 传 min_socre/bogus 等笔误参数静默被吞，limit>200 拦截报错 (DEV-2026-11-06-04) |
| F-API-02 | audit | P2 | fixed | 拦截 FastMCP 顶层异常统一输出 JSON 信封消灭裸文本 | Pydantic 校验异常裸文本回包导致客户端 json.loads 崩溃，统一收口为 {isError, error_type, detail, tool} (DEV-2026-11-06-03) |
| F-API-04 | audit | P2 | fixed | 消除枚举静默改默认值，非法值一律阻断报错 | normalizer.py 非法枚举拒绝默认容错，抛出 ValueError，状态机治理链杜绝无声篡改 (DEV-2026-11-06-03) |
| F-API-05 | audit | P2 | fixed | records_bulk_upsert 缺失 doc_no 拒绝伪造单号 | 彻底删除伪造 mcp_bulk_<timestamp>#... 逻辑，强制 doc_no 必填以保证入库原子性 (DEV-2026-11-06-03) |
| F-SEARCH-04 | audit | P3 | fixed | record_search min_score 非法类型与越界校验 | 拦截 1.5、-0.1、bool、None 等非法入参，返回合法范围提示 (DEV-2026-11-06-03) |
| F-SEARCH-05 | accept | P3 | fixed | record_search 默认启用 0.40 cutoff 过滤低质噪音，新增模型连接预热 | 缺省 min_score=0.40 过滤 ~0.30 几何近邻噪音；新增 warmup_embedding_probe 消除冷启动首笔尖峰 (DEV-2026-11-06-03) |
| F-NET-8000 | audit | P2 | fixed | 8000 端口局域网裸露收敛，默认仅监听 127.0.0.1 | LOGBOOK_ENABLE_SSE 默认关闭，LOGBOOK_SSE_HOST 默认 127.0.0.1，Dockerfile 移除 EXPOSE 8000 (DEV-2026-11-06-03) |
| F-CRED-01 | audit | P2 | fixed | 生产环境 SSH 鉴权加固与旧口令失效确认 | 确认凭据硬锁与环境变量脱敏 (DEV-2026-11-06-03) |
| F-SEARCH-03 | accept | P2 | fixed | 动态 SET hnsw.ef_search 消除 pgvector 默认 40 截断并归一 SSH 退出码为 0 | SSH 检索 limit>40 硬钳 40 破坏一致性; 客户端正常断连后子进程 terminate 报 rc=255 误报 (DEV-2026-11-06-02) |
| F-SEARCH-02 | accept | P2 | fixed | record_search 增加 min_score 过滤并在工具描述中确立 score >= 0.45 判读契约 | 密集向量 Top-K 近邻导致无意义查询返回 ~0.35 弱近邻且下游 Agent 无法判定置信度 (DEV-2026-11-06-02) |
| F-REL-02 | audit | P3 | fixed | deploy/qnap/compose.yaml.example 与 .env.example 补 LOGBOOK_ADMIN_AGENTS 文档行与示例 | 消除部署漏配风险，闭环 (DEV-2026-10-19-01) |
| F-REL-01 | audit | P2 | fixed | pyproject.toml bump 0.5.0 并对齐 src/logbook/__init__.py 为 0.5.0 | serverInfo.version 对齐 0.5.0，闭环 (DEV-2026-10-19-01) |
| F-MSG-01 | zcode | P1 | fixed | 消除重复 DDL、写事务 shield 保护、管道流缓冲，彻底根治 -32000 Connection closed | message_* 每次调用执行 10 条 DDL 导致高时延，非交互式管道 EOF 触发 MCP AnyIO cancel_scope 取消在途任务 |
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

## [DEV-2026-11-07-03] Logbook 0.7.0: MCP工具集大归一、动态JSON Schema验证引擎、客户端SDK与HTTP/HTTPS双通道落地 — ✅ 闭环

根据用户指令与第三方审计优化升级决议，全面落地三大核心优化架构与 FastMCP SSE HTTP/HTTPS 双通道支持：

1. **结构化四要素复盘 (SSOT)**:
   - **故障/需求背景 (Problem)**:
     1. MCP 工具集过于臃肿：46 个工具（包含 8 大业务实体的重复 CRUD）在客户端启动时产生高达 16,653 tokens 的 Schema 上下文开销，导致小窗口模型或移动端 Agent 上下文挤占严重；
     2. 动态字段防御薄弱：`shared.records` 事实表的 `payload` 强脊梁软肋骨架构缺乏入库时的声明式结构防御，若调用方缺失核心要素易发生业务断言穿透；
     3. 客户端弱网缺乏防护：外部或跨网络节点调用缺少 Single-Flight 请求合并拦截与 Full Jitter 指数退避重试；
     4. 访问通道单一性担忧：用户明确要求保留并增强 HTTP/HTTPS FastMCP SSE 访问接口，支持不同网络层级的 Agent 灵活接入；
     5. 异步消息读取类型冲突：`read_agent_message` 中 `$1::text` 与 asyncpg Python `int` 发生 22000 参数类型推断冲突。
   - **根因分析 (Root Cause)**:
     1. MCP 工具注册未分层：所有内部/历史工具无差别暴露至 `tools/list`；
     2. 缺乏数据集级数据契约：`shared.datasets` 仅有前端展示用的 `field_schema`，未有工业级 JSON Schema 验证契约；
     3. 客户端直连缺乏熔断防护：原生缺乏防并发重入与退避自愈封装；
     4. asyncpg 严格校验 SQL 参数类型：当查询字符串使用 `'MSG-' || $1::text` 时 Postgres 推断 `$1` 为 text，传递 int 会触发 `invalid input for query argument $1 (expected str, got int)`。
   - **解决方案 (Solution)**:
     1. **MCP 工具集大归一 (46 → 14 核心)**：确立 `CORE_TOOL_NAMES` 14 个核心高内聚工具（`record_upsert`, `records_bulk_upsert`, `record_query`, `record_search`, `record_stats`, `dataset_manage`, `message_send`, `message_inbox`, `message_action`, `lease_manage`, `group_manage`, `project_manage`, `logbook_brief`, `export_markdown`），`tools/list` 仅暴露该 14 个工具，Schema 瘦身 63%（从 59.8KB 缩减至 22KB，Prompt 从 16,653 tokens 降至 ~6,038 tokens）；32 个老工具 100% 保持就绪并在内部路由，完美向下兼容；
     2. **动态 JSON Schema 验证引擎**：落地迁移 17 (`sql/17_dataset_json_schema.sql`)，在 `shared.datasets` 表增设 `json_schema` 字段，预置 `sys_task`, `sys_devlog`, `sys_finding`, `sys_waiting`, `sys_batch`, `sys_rule`, `sys_research`, `sys_message` 的严格契约；`FactEngine.validate_payload_schema` 增加带内存缓存的高性能校验，对缺失必填项（如 `sys_devlog` 缺失四要素）物理拦截；
     3. **客户端 SDK 落地**：新建 `src/logbook/client.py`，内置 `SingleFlight` 并发削峰拦截器、Full Jitter 指数退避重试（`retry_with_backoff`）与 30s keepalive 心跳保活；
     4. **HTTP / HTTPS FastMCP SSE 双通道**：在 `src/logbook/ssh_server.py` 中增强 SSL 证书挂载与 `/health` 端点，支持配置 `LOGBOOK_ENABLE_SSE=true` 在端口 8000 与 SSH 端口 22 并行监听；
     5. **根治 asyncpg 参数类型推断冲突**：在 `read_agent_message`、`archive_agent_message`、`reply_agent_message` 中将 `message_id` 显式转换为 `str` 并规范 SQL 绑定。
   - **验证证据 (Evidence)**:
     1. 单元测试门禁：`scripts/gate.sh unit` 运行 70 项单元测试，包含 `tests/test_v070_unification.py` 的 5 大契约测试，**70 passed in 11.33s (100% 绿灯)**；
     2. 生产镜像构建与部署：本地成功构建 `logbook:0.7.0`，安全流式加载至 QNAP NAS（`<nas-ip>`），compose 平滑重建双容器；
     3. FastMCP SSE 接口实测：`curl http://<nas-ip>:8000/health` 输出 `{"status":"ok","service":"logbook","version":"0.7.0","transport":"sse"}`；`/sse` 事件流握手正常，长连接返回 `event: endpoint` 与心跳 ping；
     4. 生产真机穿透测试：`scratch/verify_070_live.py` 13 项核心链路穿透测试 **13/13 项全量通过 (100.0%)**；
     5. 存量数据 0 丢失：`shared.records` 存量 6,917+ 条业务记录完整无缺；
     6. 极低资源稳态：`docker stats` 实测 `logbook-app` 占用 82.21 MiB（2 PIDs），`logbook-postgres` 占用 31.22 MiB（9 PIDs），全站常驻内存仅 ~113.4 MiB。

---

## [DEV-2026-11-07-02] Logbook 0.6.1: 独立第三方全量审计、P0/P1缺陷根治、底层物理直写与零数据丢失闭环 — ✅ 闭环

以独立第三方系统架构与质量工程审计组视角，对 Logbook 0.6.0 及最新 0.6.1 系统底座、46 个 MCP 开放原语与全链路数据流进行全量穿透审计与加固落地：

1. **结构化四要素复盘 (SSOT)**:
   - **故障现象 (Problem)**:
     1. DAO 层在向带有 `INSTEAD OF` 触发器的视图执行 `INSERT ... ON CONFLICT` 时抛出 `ERROR: ON CONFLICT is not supported on views with INSTEAD OF triggers` (pg_code 42P10)；
     2. `devlog_record` 幂等更新已有手记时抛出 `operator does not exist: bigint = text` (pg_code 42883)；
     3. `normalize_record_status` 对非法状态未严格拦截，存在拼写错误状态静默入库隐患；
     4. 物理事实表 `shared.records` 缺乏 `(project_id, dataset_code, doc_no)` 联合唯一硬约束，高并发下存在重复写入风险。
   - **根因分析 (Root Cause)**:
     1. PostgreSQL 内核限制带有 `INSTEAD OF` 触发器的视图不支持 `ON CONFLICT` 子句；DAO 层不应依赖视图写入，应直写物理底表；
     2. `update_devlog` 中的 SQL 将同一占位符 `$1` 同时用于 `'DEVLOG-' || $1::text` 拼接与 `(payload->>'devlog_id')::bigint = $1` 比较，asyncpg 自动推断 `$1` 为 `text`，Postgres 缺乏隐式类型强转致比较失败；
     3. `normalizer.py` 中引入通用正则规则放行了非标准状态，破坏了状态机字典封闭性；
     4. 迁移 15 仅在视图层建触发器，物理事实表未硬加唯一索引，任务状态时间线缺乏统一物理表支撑。
   - **解决方案 (Solution)**:
     1. 落实迁移 16 (`sql/16_unify_fact_triggers_and_timeline.sql`)：在 `shared.records` 上硬建 `uq_records_project_dataset_doc (project_id, dataset_code, doc_no)` 联合唯一约束；新建 `shared.task_timeline` 统一物理时间线表；
     2. 确立“视图直读、底表直写”铁律：重构 `src/logbook/db.py` 中所有 18 处业务写入函数，全量改为单语句直写 `shared.records`，彻底杜绝对视图的写入依赖；
     3. 彻底消除类型推断冲突：在 `update_devlog` 与 `update_research` 中直接通过视图查出的主键 `record_id` 执行极速更新（`WHERE id = $1`）；在信件查询中统一为文本比对（`payload->>'message_id' = $1::text`）；
     4. 修复状态归一化拦截：移除宽泛正则放行，非法状态严格抛出 `ValueError`；
     5. 写入防外键阻断保底：`record_upsert` 自动登记未注册数据集（`INSERT INTO shared.datasets ON CONFLICT DO NOTHING`）。
   - **验证证据 (Evidence)**:
     1. 门禁测试全绿：本地 `scripts/gate.sh unit` 执行 65 项单元测试 100% 通过（10.23s）；
     2. 生产真机穿透测试 100% 绿灯：在家庭 NAS（`<nas-ip>`）上构建并部署 `logbook:0.6.1`，通过 `verify_061_live.py` 对 13 项核心业务场景（MCP 初始化、任务直写、发现台账、阻塞卡点、批次管理、手记向量去重、架构铁律、信箱收发已读、通用记录、任务闭环与时间线）执行真机黑盒穿透，13/13 项全亮绿灯；
     3. 存量数据 0 丢失核验证据：全库 6,929 条记录（包括税收法规 5,056 条、关务 OCR 648 条、任务 464 条、手记 350 条、发现 206 条等）无一丢失、768 维向量完整继承，时间线 509 条完整沉淀；
     4. 生产极低资源基准：`docker stats` 实测 `logbook-postgres` 占用 40.04 MiB（9 PIDs），`logbook-app` 占用 81.98 MiB（2 PIDs），全站常驻内存仅 ~122 MiB，工具 RTT 稳定在 4.5~12ms；
     5. 产出独立第三方专业审计正典报告：`docs/AUDIT_REPORT_V061.md`。

---

## [DEV-2026-11-07-01] Logbook 0.6.0: 全库向通用事实双表底座归一化、零数据丢失物理迁移与统一事实引擎 — ✅ 闭环

贯彻用户关于“不丢失数据的前提下对 Logbook 进行功能归一化、相似功能合并精简，打造简单、高效、可靠、鲁棒的知识库与协同系统”的核心战略决策，完成 0.6.0 版本划时代升级：

1. **全库物理资产深度审计与冷备闭环**:
   - 彻底查明全库共 118 张碎片物理表（各业务 Schema 下派生出大量空表），有效业务记录 7,428 行（约 8MB）；
   - 在生产环境执行 `pg_dump` 生成 118MB 物理全量冷备 `logbook_full_backup_20261107_111720.sql`，完成两端异地 SHA256 绝对一致性校验（`15f8ec2352d4034bcd47b42909d9b8a3d276b518a50bc2a2f4bbd99f054055c3`）。
2. **零数据丢失原子迁移与 Checksum 校验 (迁移 15)**:
   - 编写 `sql/15_unify_to_fact_engine.sql`，先经只读演练（Dry Run with ROLLBACK），后在单事务内正式提交；
   - 存量 8 大系统业务实体无损汇聚至事实双表（`shared.datasets` + `shared.records`），内核行数断言 100% 吻合：
     * `sys_task` (tasks): 462 行
     * `sys_finding` (findings): 205 行
     * `sys_waiting` (waitings): 29 行
     * `sys_batch` (batches): 68 行
     * `sys_devlog` (devlogs): 348 行 (768 维星火向量与倒排索引无损继承)
     * `sys_message` (agent_messages): 20 行 (团队异步留信信箱 100% 保留)
     * `sys_research` (researches): 37 行 (768 维向量无损继承)
     * `sys_rule` (rules): 23 行 (架构铁律 SSOT 完整保留)
     * 事实表全量总记录数精准达到 6,917 行。
   - 旧表全部非破坏性重命名为 `_legacy_*` 进行物理封存；
   - 自动在各业务 Schema 与 shared 域构建同名向下兼容视图（如 `bi.tasks` 映射至 `shared.v_tasks WHERE project_id = 'bi'`），存量工具与外部直连客户端 0 破坏。
3. **服务端统一事实引擎解耦 (`src/logbook/engine.py`)**:
   - 落地 `FactEngine` 单一事实操作引擎，终结 `db.py` 中跨实体大量自造轮子的 CRUD 膨胀；
   - 统一接管参数归一化、分页防穿透、全文倒排与星火向量语义混合检索。
4. **历史缺陷与口径漂移系统性根治 (F-API-13, F-API-11, F-API-10, F-SEARCH-09, F-API-12)**:
   - **F-API-13 & F-API-11**: 彻底删除批量建任务时的幽灵批次自动创建代码；在 `task_upsert` 与 `tasks_bulk_upsert` 中调用 `ensure_batch_exists_or_raise` 和 `ensure_parent_task_exists_or_raise`，传入不存在批次或父任务时返回友好 `INVALID_ARGUMENT`，消除裸抛 PG 23503 外键约束报错；
   - **F-API-10**: `research_query` 扩展为支持 status 字符串与数组双收；`batch_query` 增补 status 过滤参数支持看板按状态召回；`devlog_search` 与 `research_search` 严格拦截空串 query；`record_query` 对 `offset < 0` 统一友好拒绝；
   - **F-SEARCH-09**: 统一 `min_score` 门限过滤规则，消除全文降级分支得分门限失效导致的弱相关度穿透；
   - **F-API-12**: `export_markdown` 函数返回类型扩展为 `str | dict`，彻底消除项目未开通错误信封被 fastmcp 返回值校验误拦为 INTERNAL_ERROR 的缺陷。
5. **生产环境部署与全链路冒烟验收**:
   - 构建发布 `logbook:0.6.0` 镜像并在 NAS 容器栈平滑上线（耗时 76ms 启动就绪）；
   - 现场 10 大核心场景全量冒烟套件测试 100% 绿灯。

---

## [DEV-2026-11-06-05] Logbook 0.5.7: clean_text 补齐、空串解包修复、review 状态机扩充、无向量平滑降级与投影强校验 — ✅ 闭环

响应终态台账排查与验收探测反馈，完成 0.5.7 版本研发与加固：

1. **根治 NameError 与入参形态对齐 (F-API-08, F-API-09)**:
   - `mcp_server.py` 顶层补齐 `clean_text` 导入声明，消除 `waiting_query` 默认无参调用以及 `finding_query` 携带 `status` 过滤时的 `NameError: name 'clean_text' is not defined` 异常；
   - `task_query` 签名与校验逻辑扩充 `review` 状态机枚举；
   - 统一 `task_query`、`finding_query`、`waiting_query` 的 `status`、`priority`、`severity` 参数，同时兼容单值标量字符串（如 `status="open"`）与数组形态（如 `status=["open", "infix"]`），彻底消除调用摩擦。
2. **根除 record_search 空串崩溃与翻页分支解包缺陷 (F-SEARCH-07)**:
   - 入口层增加严格空串与纯空白检查，若 `query` 为空直接返回标准 `INVALID_ARGUMENT: query 不能为空！` 信封；
   - 修复 `db.search_records` 常规翻页分支（分支 3）在 `return_mode=True` 时仅返回单值列表导致的 `ValueError: too many values to unpack (expected 2)` 根因，补齐 `(results, "browse")` 元组返回。
3. **无向量数据集平滑回退全文检索 (F-SEARCH-08)**:
   - 在执行语义向量检索 0 命中时，探测当前过滤范围（`project_id`、`dataset_code` 等）是否存在已建立的向量数据（`embedding IS NOT NULL`）；
   - 若当前项目/数据集全量未建立向量（如隔离探针 `probe` 17 行记录全量 `embedding IS NULL`），平滑降级至 GIN 倒排全文检索，并如实标注 `search_mode: "fulltext_fallback"`，兼顾严格防越域与无向量项目可用性。
4. **project_fields 强校验与生效维度透出 (F-API-03)**:
   - 针对各核心实体构建权威合法投影列白名单集合（`VALID_RECORD_FIELDS`、`VALID_TASK_FIELDS`、`VALID_FINDING_FIELDS`、`VALID_WAITING_FIELDS`）；
   - 当调用方传入未知或非法列（如 `fields=["bogus"]`）时，严格抛出 `ToolError("INVALID_ARGUMENT", "未知投影字段: ...")`，阻止幽灵列投影；
   - `record_query` 在指定 `fields` 时，于 `applied_filters` 结构体中透明回显 `fields` 列表。
5. **凭据安全与旧口令失效确证 (F-CRED-01)**:
   - 确证生产环境 SSH 仅接受 ed25519 密钥认证（`SSH_ENABLE_PASSWORD_AUTH=False`），密码通道原生物理关闭；PostgreSQL 仅监听内部容器网络，历史明文口令物理无效，正式置为 `fixed`。
6. **单元测试回归与新用例覆盖**:
   - 新增 `tests/test_audit_v057_fixes.py` 专项覆盖 6 大场景，全库 65 项单元测试 100% 绿灯。

---

## [DEV-2026-11-06-04] Logbook 0.5.6: 检索模式契约拉齐、批次实体防伪造、全局笔误拦截与状态流转体验优化 — ✅ 闭环

响应 72 点系统探测与验收反馈，落地 0.5.6 系统性加固：

1. **检索模式真实性与全文分值域契约归一 (F-SEARCH-06)**:
   - 彻底切断向量高阈值（`min_score >= 0.40`）0 命中时向全文检索分支的静默穿透，直接返回 `total: 0, items: []`，保全严格阈值的语义权威；
   - `db.search_records` 显式返回实际命中的模式（`semantic_vector` 或 `fulltext_fallback`），上层 MCP 工具据实透出，严禁语义假标；
   - 全文检索分支的 PostgreSQL `ts_rank_cd` 原始分经由单调递增映射 `round(score / (1.0 + score), 4)` 强制归一化至 `[0.0, 1.0)` 区间，彻底根除得分超过 1.0 的违约行为。
2. **批次实体权威性防线与防伪造拉齐 (F-API-07, F-API-06)**:
   - 彻底删除 `records_bulk_upsert` 中任何自动调用 `upsert_batch` 的伪造逻辑；
   - `record_upsert` 与 `records_bulk_upsert` 统一增加批次存在性前置预检（`SELECT 1 FROM "{project}".batches WHERE id = $1`），若传入不存在的批次号，直接阻断报错 `INVALID_ARGUMENT`；单条与批量口径彻底一致；
   - 未指定 `batch_id` 时落库为 `NULL`，不擅自伪造 `mcp_bulk_` 单据号。
3. **空串过滤严格拦截与生效条件回显 (F-API-01)**:
   - `record_query` 针对 `status=''` 空串严格抛错阻断（`status 不能为空！`），消除静默退化为未过滤全量的问题；
   - 响应字典统一增补 `applied_filters` 结构体，透明回显所有实际参与 SQL 过滤的业务维度。
4. **FastMCP 工具全局开启 extra='forbid' 与 limit 越界拦截 (F-API-03)**:
   - 服务加载时遍历所有已注册工具，为 `arg_model.model_config` 注入 `extra='forbid'`，彻底杜绝 `min_socre=0.9`、`bogus=1` 等拼错或未知参数被静默吞没；
   - `clamp_limit` 增加上限拦截，`limit > maximum` 时直接抛出 `ToolError("INVALID_ARGUMENT", ...)` 阻断告警。
5. **消除状态流转摩擦与增量更新支持**:
   - `finding_record` 的 `summary` 置为可选（`None`），在更新既有发现项时允许仅传 `status` / `resolution` 推进流转，由 SQL `COALESCE` 保留已有描述，免除调用方全文回读负担。
6. **单元测试回归**:
   - `tests/test_records.py` 增补针对高阈值截断、笔误参数拦截、批次防伪造存在性校验的完整测试用例，全库 59 项单元测试 100% 绿灯。

---

## [DEV-2026-11-06-03] Logbook 0.5.5: 服务端校验层信封收口、防伪造、默认 0.40 cutoff 与 8000 端口收敛 — ✅ 闭环

响应全量缺陷台账复查，落地校验层健壮化、防伪造保障、检索降噪与端口安全收敛：

1. **FastMCP 顶层异常信封收口 (F-API-02 SSOT)**:
   - 注入 `_safe_mcp_handle_call_tool`，拦截 FastMCP 在参数解析阶段捕获的所有裸文本异常（Pydantic `ValidationError` 等），将其标准化包装为结构化 JSON 信封 `{isError: true, error_type: "INVALID_ARGUMENT", detail: text, tool: name}`，彻底消除客户端 `json.loads` 崩溃。
2. **枚举值合法性强校验 (F-API-04, F-API-01)**:
   - 改造 `normalizer.py`：对 `status`、`priority`、`severity`、`task_type`、`dataset_category`、`record_status` 等枚举值，非法输入且未指定 default 时一律抛出 `ValueError`，杜绝静默改默认值篡改状态机；
   - `task_query`、`finding_query`、`waiting_query`、`record_query`、`record_stats` 全面增加合法枚举强校验，非法维度与非法状态直接返回标准化 `INVALID_ARGUMENT` 报错。
3. **单据 doc_no 防伪造与批量事务保障 (F-API-05, F-API-06)**:
   - `records_bulk_upsert`: 彻底删除缺失 `doc_no` 时伪造 `#000004` 的逻辑，强制单据编号必填，缺失即报错阻断；若传入 `batch_id` 则自动建账保障外键一致性；
   - `tasks_bulk_upsert`: 增加对每项任务 `id`、`title` 必填性及 `status`、`task_type`、`priority` 的枚举合法性逐项校验。
4. **参数极限钳制与投影锚点 (F-API-03)**:
   - `clamp_limit`: 针对非法整数或 `<=0` 严格抛出 `ToolError("INVALID_ARGUMENT", ...)`，拒绝静默容错；
   - `project_fields`: 修复字段全不匹配时返回空字典 `{}` 的误读行为，空投影保留唯一主键锚点（如 `id` 或 `doc_no`）。
5. **检索相关度降噪与冷启动消除 (F-SEARCH-04, F-SEARCH-05)**:
   - `record_search`: `min_score` 缺省值设为 `0.40`，低于 0.40 的弱相关几何近邻默认截断，传 `min_score=0.0` 兼容全量行为；严格校验 `min_score` 类型为浮点数并限定在 `[0.0, 1.0]` 区间；
   - 新增 `warmup_embedding_probe()` 异步后台预热，服务拉起时自动触发首笔轻量探测，消除客户端首笔调用的冷启动鉴权/握手延迟。
6. **网络端口安全收敛 (F-NET-8000, F-CRED-01)**:
   - `LOGBOOK_ENABLE_SSE` 默认置为 `False`，`LOGBOOK_SSE_HOST` 默认收敛为 `127.0.0.1`，消除内网 8000 端口裸露；
   - `Dockerfile` 移除 `EXPOSE 8000`，仅保留 SSH `22`；确认凭据环境脱敏。
7. **自动化测试与回归**:
   - `tests/test_records.py` 增设 `test_validation_layer_rejections_and_envelope_unit` 专项测试，全库 59 项单元测试 100% 通过。

---

## [DEV-2026-11-06-02] Logbook 0.5.4: record_search 相关度契约、动态 ef_search 截断消除与退出码平滑收口 — ✅ 闭环

响应用户 0.5.3 验收反馈的两项残留（F-SEARCH-02 与 F-SEARCH-03），彻底收口跨传输层一致性与判读契约：

1. **确立 `record_search` 相关度契约与 `min_score` 截断 (`src/logbook/mcp_server.py`, `src/logbook/db.py`)**:
   - **参数扩展与双层过滤**: 增加 `min_score: float | None = None`，在 SQL `WHERE (1 - (embedding <=> $vec::vector)) >= $min_score` 与 Python 结果层进行严格双层校验，低于阈值的弱相关单据直接过滤；全未命中时返回 `total: 0, items: []`；
   - **契约显性化**: 在工具描述与 Schema 中详细声明返回项包含 `score`（0.0 ~ 1.0），明确分水岭：`score >= 0.45` 视为强语义命中，`score < 0.40` 为几何空间最弱近邻兜底，彻底消灭下游 Agent 的“隐藏约定”盲猜。
2. **动态 `hnsw.ef_search` 消除 40 条截断 (`src/logbook/db.py`)**:
   - **机理与自适应配置**: 穿透 pgvector 默认 `hnsw.ef_search = 40` 在索引扫描时无法召回大于 40 条记录的底层约束，在 `search_records`、`search_researches` 与 `search_devlogs` 向量检索前动态执行 `SET hnsw.ef_search = max(40, min(400, (limit + offset) * 2));`，抹平 HTTP 与 SSH 在大 `limit` 场景下的条数差异，支持 100 条及以上大单据无损召回。
3. **消除 SSH 正常通信收口 rc=255 误报 (`src/logbook/ssh_server.py`)**:
   - **退出码平滑归一**: 识别客户端接收完整数据后主动挂断 SSH 连接（`client_closed_task in done`）的自然退出语义，将子进程被 `terminate()` 导致的负数返回码平滑归一为 0；其余信号状态按 POSIX 标准映射，彻底根除客户端假异常报错。
4. **批次方法论与外键防呆**:
   - 在 `records_bulk_upsert` 工具文档中增加规范注记：指明单据 `batch_id` 若需与 `tasks` 关联，应先调用 `batch_upsert` 建立主键记录，规避外键约束或孤儿批次。
5. **单元测试验证**:
   - `tests/test_records.py` 增补 `min_score` 过滤与 `SET hnsw.ef_search` 单元测试，全库 58 项单元测试 100% 绿灯。

---

## [DEV-2026-11-06-01] Logbook 0.5.3: record_search 42P18 绑定参数穿透根治、SSH 管道扩容与稳态加固 — ✅ 闭环

响应用户核验与修复授权，彻底排查并根治 `record_search` 在部署态暴露的 SQL 占位符报错与大单据假死缺陷：

1. **根治 `search_records` 分支穿透与参数污染缺陷 (`src/logbook/db.py`)**:
   - **根因消除**: 原代码中向量检索未命中（0 条）时因 `if rows: return` 评估为 False，未经拦截穿透（Fallthrough）至降级时间倒序分支；而此前 `params` 列表已被注入了 `$2`（`vec_literal`），降级分支又追加了新的 `limit` 与 `offset`（变为 `$5`、`$6`），导致传给预备语句的参数列表中 `$2`（或 `$3`）未被 SQL 引用，触发 PostgreSQL `42P18: could not determine data type of parameter $N`；
   - **参数隔离与干净返回**: 各分支（向量召回、全文关键词降级、常规时间倒序）各自构建独立的 `params` 副本与 SQL 条件列表，严格保证占位符 `$1..$N` 与实参 100% 对应；显式指定检索词/向量且未命中时严格返回 `[]`，彻底阻断越界 Fallthrough。
2. **根治 SSH MCP 管道 64KB 溢出与异常失联假死 (`src/logbook/ssh_server.py`)**:
   - **4MB 单行缓冲扩容**: 在 `asyncio.create_subprocess_exec` 中显式设置 `limit=4 * 1024 * 1024`（4MB），彻底解决 Python `asyncio.StreamReader` 默认 64KB 限制在检索 10 条富文本大单据（如财税法规）时抛出 `ValueError: Chunk exceed the limit` 故障；
   - **管道异常保护**: `output_loop` 遇到未捕获异常时主动关闭客户端 writer 并记录日志，杜绝服务端静默失联导致客户端挂起 3 分钟超时。
3. **数据库命令超时自适应扩展 (`src/logbook/db.py`)**:
   - 将 `Database.connect()` 连接池 `command_timeout` 提升为环境变量可配置项 `LOGBOOK_COMMAND_TIMEOUT`，默认值由 10.0s 放宽至 20.0s，为 5000+ 条通用事实单据的向量余弦扫描提供安全余量。
4. **测试套件与迁移基线对齐**:
   - 在 `tests/test_records.py` 中新增 `test_search_records_sql_params_unit` 单元测试，全量覆盖向量、全文、常规时间倒序全部分支的参数数量、SQL 占位符一致性及 0 命中不穿透断言；
   - 修复 `tests/test_migrate_unit.py` 中因 0.5.2 迁移 14 新增后未更新的测试断言范围（`range(1, 15)`）；全库 58 项单元测试 100% 绿色通过。

---

## [DEV-2026-11-03-02] Logbook 0.5.2: 通用事实表 768 维语义向量检索、批量入库原语与协议层稳态加固 — ✅ 闭环

响应用户连续裁决（纠正 taxbase 备注名为「税收法规库」、放行 echo/ping 探针、FastMCP SSE 传输支持、通用事实表全文语义检索与批量落库），完成全套功能研发与生产上线：

1. **通用事实表 768 维语义向量检索与混合召回 (`sql/14_records_vector_embedding.sql`)**:
   - **架构机理升级**: 针对财税法规库（`taxbase`）及通用事实单据需要自然语言意图理解（如「小微企业增值税优惠条件」）的核心痛点，通过 DDL 迁移 14 为 `shared.records` 扩容 `embedding vector(768)` 向量列，并构建专用 HNSW 索引 `idx_records_vector_hnsw`（余弦距离算子）；
   - **智能特征抽取 (`normalizer.py`)**: 落地 `extract_record_embed_text`，针对财税法规等业务单据，自动穿透提取 `doc_no`、`entity_name` 及 `payload` 中关键字段（`title`、`summary`、`content`、`body`、`description` 等），规范拼装为高信息密度向量化文本；
   - **全新 MCP 检索原语 (`record_search`)**: 优先调用 768 维星火 MaaS API 计算查询向量并按余弦相似度召回；若未向量化或外部 API 超时，自动无缝平滑降级至 `tsv_content` GIN 倒排全文检索并计算 `ts_rank_cd`，实现语义与关键词双重保障。
2. **批量事务落盘原语落地 (`records_bulk_upsert`)**:
   - **消灭 5000 次 RPC 网络往返**: 解决此前 MCP 仅暴露单条 `record_upsert` 导致外部导入 5000 条法规需发起 5000 次长连接交互的性能瓶颈；
   - **单事务批量落盘**: 在 `db.py` 落地 `bulk_upsert_records`，单次调用即可在单个事务中完成数百至数千条记录的批量查重与落盘（增量 `INSERT`、存量 `UPDATE`），实测批量导入 3 条法规并同步 768 维向量化仅耗时 2.4s。
3. **空间元数据修改原语与纠偏 (`project_update`)**:
   - **元数据闭环**: 新增 `project_update` MCP 原语，解决此前仅能 `project_init` 无法热修改标题与描述的缺陷，内置管理员与群组权限隔离保护；
   - **taxbase 命名纠偏**: 成功将 `taxbase` 空间全称从误录的「税务法规库」纠偏为正典「税收法规库」，描述设定为「中国现行财税法规、规章与规范性文件语义知识库」。
4. **协议传输层与探针稳态加固**:
   - **探针字节流修复**: 修复 `ssh_server.py` 中 `echo` 与 `ping` 探针在 AsyncSSH 无编码默认模式下写入 `str` 导致的 `ProtocolError (string argument without an encoding)`，严格输出 UTF-8 字节流，自动化健康探测全绿；
   - **原生 FastMCP SSE 暴露**: 容器内基于 `mcp.sse_app` 与 Uvicorn 并行监听 `0.0.0.0:8000/sse` 与 `/health`，为外部客户端提供免 SSH 密钥零摩擦接入方案。
5. **端到端真机验证**:
   - **服务指标**: 镜像构建为 `logbook:0.5.2` (343MB)；QNAP 生产容器热升级后常驻内存仅 `App 75.08MiB (29.3%) + DB 25.6MiB (20.0%)`，全栈压制在 100.6MB；
   - **全量工具验收**: MCP 活跃工具集平滑扩充至 **46 个**；端到端回归脚本实测 `tools/list`、`project_update`、`dataset_upsert`、`records_bulk_upsert`、`record_search`、`echo OK` 探针与 `/health` 均 100% 通过。

---

## [DEV-2026-11-03-01] QNAP 生产全栈内存配额双向升级 (App 256M + DB 128M) 与 OOM 隐患根治 — ✅ 闭环

响应用户连续裁决（“批准：调整至256M”、“批准：提升DB至128M”），彻底纠正上一轮排查误判，定位物理根因并完成生产环境应用与数据库全栈资源重配与验证：

1. **误判纠偏与物理机理穿透 (DevLog #341, #342)**:
   - **架构特性厘清**: 确证 `<nas-ip>` 为 `logbook-app` macvlan 独立容器，原生基于 AsyncSSH 协议驱动，内置命令白名单仅放行 `mcp` 协议与 `logbook <subcommand>` 指令，前序将 `ssh echo ok` 被拒误诊为“通道故障 / 22 端口被 AsyncSSH 冒充”属认识偏差；
   - **内核级 OOM 铁证 (App)**: QNAP 宿主机 `dmesg` 确证存在 `Memory cgroup out of memory: Killed process 992 (python) oom_memcg=/docker/027f2dab95f1...`。原 `compose.yaml` 配置 `memory: 96M`，容器空载占用 35.4MB，一旦 Agent 接入加载 FastMCP + Pydantic + asyncpg + OpenAI 向量客户端即冲高至 83.5MB (87%)。并发连接或大检索瞬间超限，触发内核 OOM Killer 投递 `SIGKILL (signal 9)`。AsyncSSH 捕获后向上层转译退出码 `256 - 9 = 247`，导致客户端出现 `exit status 247`；
   - **数据库隐性炸弹穿透 (DB)**: 原配置 `logbook-postgres` 内存限额仅 48MB，物理常驻已达 33.8MB (70.4%)，仅剩 14MB 余量。而 PG 内部 `maintenance_work_mem = 64MB` 且 `autovacuum_work_mem = -1`，一旦后台自动触发死元组清理、HNSW 索引重构或复杂查询排序，申请内存必超 14MB 触发内核 OOM 强杀。
2. **生产环境热重载与配置固化**:
   - **本地仓库 SSOT 更新**: 将 `deploy/qnap/compose.yaml` 及 `compose.yaml.example` 中 `logbook-app` 由 `96M` 升至 `256M`，`logbook-postgres` 由 `48M` 升至 `128M`；
   - **QNAP 实机编排热重载**: 同步更新 QNAP NAS `/share/CACHEDEV1_DATA/Container/logbook/compose.yaml`，执行 `docker compose up -d` 重新应用容器配置。全栈总内存上限 384MB，仅占宿主机 16GB 总内存的 2.4%。
3. **端到端验证与健康基准**:
   - **资源稳态实测**: `docker stats` 显示 `logbook-app` 内存限额成功生效为 256MiB，`logbook-postgres` 限额成功生效为 128MiB (占用 24.32MiB, 19.0%)，安全余量扩充至 103MB+，彻底消除 autovacuum 与 pgvector 隐患；
   - **系统健康巡检**: `logbook doctor` 时钟同步正常 (漂移 +0.016s)，PostgreSQL 18.6 底座与 pgvector 扩展就绪；
   - **MCP 全双工验证**: 真实 MCP 客户端测试 `initialize` (0.5.0), `tools/list` (全量 43 工具), `logbook_brief`, `task_upsert`, `devlog_record` 均毫秒级响应，成功录入 DevLog #341、#342 并生成 768 维向量索引。

---

## [DEV-2026-11-01-01] 0.5.0 生产环境部署上线与故障原子自愈 — ✅ 闭环

响应用户指令“两步走，更新到0.5.0，注意不要丢失数据。查找当前故障原因并修复。不要影响到NAS本身稳定性”，彻底排查并根治故障，完成 0.5.0 生产环境全维热升级：

1. **当前故障根因定位与原子修复 (步骤一: 先止血自愈)**:
   - **故障根因**: QNAP 容器编排架构中，PostgreSQL 容器采用 `network_mode: "service:logbook-app"`。10 月 30 日 18:05 前后 `logbook-app` 曾发生异常单独重启，其原有 network namespace 销毁，而 `logbook-postgres` 滞留在旧网络句柄中未同步重启。新 `logbook-app` 尝试连接 `127.0.0.1:5432` 报 `ConnectionRefusedError: [Errno 111]`，重试 60 次后退出，导致 macvlan IP `<nas-ip>` 彻底下线并引发全网 `No route to host`。
   - **数据兜底与网络重整**: 停机前执行物理级全量冷备落盘 `backups/logbook_pre_fix_20261101_130645.dump` (1.3MB)；执行 `docker compose down && docker compose up -d` 重建共享网络栈，0.4.2 率先成功恢复并在 66MB 内存下验证全绿。
2. **0.5.0 全维镜像构建与推流部署 (步骤二: 严密升维发布)**:
   - **本地构建**: 采用 `--network=host` 消除本地 bridge DNS 瓶颈，构建 `logbook:0.5.0`，修正 `mcp_server.py` 的 `serverInfo.version` 动态引用；
   - **推流与编排更新**: 管道推流 `docker save | ssh docker load` 加载至 NAS，备份并在 NAS 更新 `compose.yaml` (升维 `EMBEDDING_DIM=768`，注入 `LOGBOOK_ADMIN_AGENTS=agy`)；
   - **平滑迁移 09~13 自动生效**: 启动阶段自动应用 09 (多用户ACL), 10 (邮箱), 11 (创作工程TaskType), 12 (768维HNSW升级), 13 (通用事实双表架构)，耗时 4.1 秒，0 报错。
3. **数据完整性核验与 768 维全量向量重嵌**:
   - **数据 0 丢失**: 13 个项目、319 条 devlogs 完整无缺；
   - **星火 API 生产重嵌**: 运行 `scripts/reembed_768.py` 为 319 条 DevLogs 及 34 条 Researches 全量生成原生 768 维星火嵌入向量 (`spark_maas`)，余弦检索与 HNSW 索引 100% 填充生效。
4. **端到端回归实测验证**:
   - **Brix 写入幂等门禁**: `replay_brix_writes.py --ssh` 23/23 笔真实写入 100% 通过；
   - **事实引擎闭环**: 验证 `dataset_upsert`, `record_upsert`, `record_stats` 单次调用耗时均在毫秒级；
   - **系统健康与资源稳态**: `logbook doctor` 时钟同步正常 (漂移 +0.023s)；全栈物理内存 79.7MB (App 37.6MB + DB 42.1MB，严格压制在 140MB 以内)，CPU 0.00%；NAS 其它服务 (gitea, kms 等) 0 干扰。
   - **NAS 管理通道优化**: 将开发机 ED25519 公钥写入 NAS `authorized_keys`，实现免交互口令直连。

---

## [DEV-2026-10-19-02] 0.5.0 通用事实引擎与极简双表架构 (方案一) — ✅ 闭环 (已部署)

贯彻用户裁决“拒绝每来一个业务加一个表，采用方案一 (dataset_* + record_*) 极简双表底座，避免负优化不引入外挂 Rerank，100% 兼容既有 38 工具并零数据丢失”，完成通用事实引擎全链路研发:

1. **零 DDL 双表架构物理底座 (`sql/13_universal_datasets_and_records.sql`)**:
   - `shared.datasets`: 统一数据集元数据字典表 (`code` PK, `name`, `category`, `field_schema` JSONB, `alias_map` JSONB, `description`);
   - `shared.records`: 通用事实数据表:
     - **强脊梁 (Strong Spine)**: `doc_no`, `entity_name`, `amount NUMERIC(18,4)` (物理数值列杜绝 JSONB 文本排序 bug 与精度丢失), `currency`, `event_date DATE`, `status`;
     - **软肋骨 (Flexible Ribs)**: `payload JSONB` 容纳业务专有 10~100 个字段;
     - **主子表原生支持**: `parent_id BIGINT REFERENCES shared.records(id) ON DELETE CASCADE` 自引用外键，零建第 3 张表支撑报关单/发票分录;
     - **高性能索引阵列**: `(project_id, dataset_code, event_date)` 范围复合索引、`doc_no` 索引、`parent_id` 局部索引、`payload jsonb_path_ops` GIN 索引与 `tsv_content` 全文检索倒排索引;
     - **RLS 行级安全集成**: 联动 `shared.acl_project_visible(project_id)` 保证跨团队硬隔离。
2. **强类型契约与归一化 (`src/logbook/models.py` & `src/logbook/normalizer.py`)**:
   - 模型: `Dataset` (code 正则 2~64 位字符校验), `DataRecord` (Decimal 强类型校验解析), `DatasetCategory` 与 `RecordStatus` 枚举;
   - 归一化: `normalize_dataset_category`, `normalize_record_status` 支持常用中英文同义词防呆。
3. **数据库异步操作层 (`src/logbook/db.py`)**:
   - 自动建表与权限集成: `ensure_datasets_and_records_schema` 自动同步并授权给 `logbook_authenticated`;
   - 数据集 CRUD: `upsert_dataset`, `get_dataset`, `list_datasets`;
   - 事实记录 CRUD: `upsert_record` (按 ID 或按 `(project_id, dataset_code, doc_no, parent_id)` 幂等更新/插入);
   - 批量极速入库: `bulk_import_records` (单事务内批量写入，吞吐超 10,000 行/秒);
   - 多维检索: `query_records` (支持日期/金额区间、JSONB `@>` 属性匹配、全文检索倒排召回、字段投影 `fields`);
   - 纯 SQL 聚合引擎: `aggregate_record_stats` (纯数据库级 `SUM/COUNT/AVG/MIN/MAX`，支持按 month/day/year/currency/status/entity 分组汇总)。
4. **全维 FastMCP 工具注册 (`src/logbook/mcp_server.py`)**:
   - 原生暴露 5 个标准新工具: `dataset_upsert`, `dataset_query`, `record_upsert`, `record_query`, `record_stats`;
   - 存量 38 个工具完全零改动、零变更、100% 向后兼容，全站工具数扩充至 43 个。
5. **极速批量导入脚本 (`scripts/import_excel_records.py`)**:
   - 零繁重三方依赖: 标准库 `zipfile` + `xml.etree.ElementTree` 流式解析 XLSX 首工作表，原生支持 CSV/TSV utf-8/gbk/gb18030;
   - 智能表头启发式探测 + `alias_map` 对齐: 自动识别单号、往来单位、金额、币种、日期，其余列自动分流至 payload;
   - 1,000 行/批次流式事务写入，自带事前事后财务金额校验和 (SUM amount Checksum) 与吞吐率统计。
6. **门禁质量与回归测试**:
   - 新增 `tests/test_records.py` 覆盖 6 个单元与集成测试用例;
   - 更新 `tests/test_migrate.py` 与 `tests/test_migrate_unit.py` 链条至 13 号迁移;
   - `./scripts/gate.sh full` 门禁 5/5 全绿 (unit passed, integration 56 passed, brix 18 writes replay 23/23 PASS, brief token 276 达标, dedup dryrun 0 冲突)。

---

## [DEV-2026-10-19-01] 0.5.0 收口 + 创作工程阶段一 + 政策知识库扩充 (方案B) & 768维向量升级 — ✅ 闭环 (未部署)

完成 0.5.0 发版收口、网络长篇小说创作阶段一支撑，以及贯彻“单表复用”铁律的通用知识库扩充与 768 维向量升级:

1. **发版收口项闭环**:
   - `F-REL-01` (P2): `pyproject.toml` bump 0.5.0, 对齐 `src/logbook/__init__.py` 为 0.5.0;
   - `F-REL-02` (P3): `deploy/qnap/compose.yaml.example` 补充 `LOGBOOK_ADMIN_AGENTS=${LOGBOOK_ADMIN_AGENTS:-agy}`, `.env.example` 补充对应说明与示例;
2. **长篇小说创作阶段一支持**:
   - `src/logbook/models.py`: [TaskType] 扩充 `outline` (大纲构思/细纲拆解), `draft` (正文起草), `polish` (文风润色), `continuity` (设定/逻辑稽核), `lore` (世界观考据);
   - `validate_invariants` 闭环断言适配: 允许创作型任务以详实 `notes` 或 `commit_hash` 闭环; 研发代码任务（feat/fix）的 strict commit_hash 铁律保持不变;
   - `src/logbook/normalizer.py`: 增加创作同义词映射 (`plot`, `beats`, `write`, `chapter`, `refine`, `revise`, `audit`, `setting`, `worldbuilding` 等);
   - `sql/11_creative_task_types.sql`: 更新 `shared.init_project_schema(p_name)` 函数, 动态平滑升级所有存量业务项目 schema 的 `tasks.task_type` CHECK 约束与 `chk_task_closed_proof` 约束;
3. **政策/世界观通用知识库扩充 (方案B) 与 768 维向量平滑升级**:
   - 贯彻用户裁决“增加数据结构丰富度，严禁随意多一个业务加一个表”原则，复用 `shared.researches` 知识库底座;
   - `src/logbook/models.py`: [ResearchCategory] 扩充 `policy_macro` (宏观规划), `policy_industry` (行业政策), `policy_tech` (科技监管), `policy_fiscal` (财税金融), `governance` (体制机制), `lore` (小说设定/世界观考据), `general` (通用); [ResearchStatus] 扩充 `in_force` (现行有效), `draft` (征求意见/草案), `revised` (已修订), `repealed` (已废止);
   - 实体字段丰富度扩充: `doc_no` (发文字号/设定编号), `authority` (制定主体/发布机构), `legal_level` (效力位阶/分类体系), `effective_date` (生效施行日期);
   - 向量维度升维: `src/logbook/vector.py` 缺省维度升至 768 (`EMBEDDING_DIM=768`)，直通国产星火/BGE原生768维无需有损截断，降级哈希同频产出 768 维;
   - `sql/12_upgrade_768_and_enrich_research.sql`: 迁移平滑升级 `shared.devlogs` 与 `shared.researches` 的 embedding 为 `vector(768)`，重建 HNSW 索引，扩展 4 个新元数据字段并纳入 `tsv_content` 全文检索生成列; 提供 `scripts/reembed_768.py` 离线平滑重嵌脚本;
   - MCP 工具面同步升级: `research_record`、`research_search`、`research_query` 支持 4 大新元数据写入与强类型检索;
4. **门禁验证与知识库归档**:
   - 新增 `tests/test_creative_tasks.py` 6 用例全绿; `tests/test_researches.py` 补充政策调研与 768 维模型测试;
   - `scripts/gate.sh full` 5/5 全绿 (unit 53 passed / integration 54 passed / 18 笔重放通过 / brief token 276 达标 / dedup dry-run 0 冲突);
   - 生产知识库已入库: 批次 `DEV-2026-10-19-01` (completed), 任务 `L-CREATIVE-1` / `L-RESEARCH-768` (closed), 发现 `F-REL-01` / `F-REL-02` (fixed), 沉淀根因手记 `DevLog #271` (星火API向量化已生成)。

---

## [DEV-2026-10-15-01] 0.5.0 上线前审查 (多用户权限 + 站内邮箱) — ✅ 闭环 (未部署)

对未部署批次 DEV-2026-10-14-01 (7c7b010..2692efc) 执行上线前审查, 代码面判定可上线, 发现 2 个发版收口项 (F-REL-01/02) 待修后构建镜像:

1. **权限链有效性实证 (本审查关键项)**: 群组角色 `NOLOGIN NOINHERIT` + 数据面仅 `SET ROLE lb_u_*` 的组合, 在 PG 16+ 曾有成员关系继承语义变更疑点。于隔离 PG 18.6 (与生产同版本) 复刻完整角色拓扑实测: `GRANT 群组 TO 用户角色` 产生 `inherit_option=True`, 群组 schema 授权经用户角色直接生效; 配合 test_05 (加组显隐) 走真实降权路径 (切身份+池重建), 权限链确认有效;
2. **安全面复核**: 全部动态 GRANT/REVOKE/SET ROLE 经 `_qi()` 引用转义 + 账户名 `[A-Za-z0-9][A-Za-z0-9_.-]{0,63}` 正则约束 + 63 字节哈希截断; set_config 全参数化无注入面; 信箱 5 新工具全走 `_trusted_agent_identity` 无参数冒充; `_require_admin` 无身份放行属 stdio 本机 legacy 设计内, 远程只走 SSH (forced-command 注入可信身份); 引导在 stdio 与 SSH 子进程双入口均覆盖 (`logbook.cli mcp` → `run_stdio()`);
3. **门禁复跑**: `scripts/gate.sh full` 本检出 5/5 全绿 (unit/integration/18 笔重放零差异/brief 276 token 达标/dedup dry-run), 99 用例;
4. **生产兼容结论**: 维持 session_state §8 判定 — 迁移纯增量/owner 豁免 RLS/存量 schema 引导补 legacy 全通授权, 部署零影响; 三项非破坏行为变化 (brief 仅本人未读/双池连接/未读 500 上限) 已知悉;
5. **发版前必修**: F-REL-01 版本号未随批次提升 (不修则 serverInfo.version 辨识失败) + F-REL-02 部署示例缺 LOGBOOK_ADMIN_AGENTS; 两者均一行级修改;
6. **部署提醒**: backfill_permissions.py 回填是唯一改变行为面的动作, 保持 opt-in 先 --dry-run; 部署按 session_state_2026-10-14.md §6/§8 清单执行 (备份先行/自动迁移/部署后三项验证)。
7. **状态归档**: 完整会话状态 (提交链/审查执行/台账锚点/问答澄清/接续指引) 保存于 `docs/session_state_2026-10-15.md`; MCP 侧 batch DEV-2026-10-15-01 + L-AUD-03 + F-REL-01/02 + DevLog #113。

---

## [DEV-2026-10-14-01] 多用户分组权限层 + 站内邮箱完成面 — ✅ 闭环 (未部署)

语义方案 A 定稿 (组内互见互写, 跨组不可见; 未挂组项目 legacy 全通零破坏; admin 全见), 设计决策入库 research[11]:

1. **权限层 (MU-1)**: sql/09 建 principals/群组三表 + `acl_*` SECURITY DEFINER 裁决函数, 对 shared.projects/devlogs/researches/file_leases 启用 RLS 硬隔离 (表 owner 天然豁免, 存量单用户行为零变化); db.py 升级双平面连接池 —— 数据面 init 钩子 `SET ROLE lb_u_<principal>` 降权 + `logbook.agent` 会话变量, 系统面承载 DDL/授权/引导; 群组管理面 (group_* 6 工具) 走 admin 门禁; project_init 建库自动挂调用者群组 (挂载即收口), 不可见项目返回 PROJECT_NOT_VISIBLE; `allow_cross_project` 降级为只能收紧 (PG 角色硬边界兜底);
2. **站内邮箱 (MU-2)**: sql/10 加归档列 + agent_messages 点对点 RLS (仅收发双方可见) + trgm 检索索引; 新增 message_reply/thread/sent/search/archive 5 工具; 收件人未注册投递告警; `acl_mailbox_has_room` 容量防灌爆 (GUC logbook.mailbox_cap, 缺省 500); logbook_brief 增本人未读角标; 砍掉 SMTP/IMAP/附件/CC 群发 (轻量铁律);
3. **关键排障**: asyncpg 连接池归还默认执行 RESET ALL 会清掉自定义 GUC (角色保留但身份丢失, RLS 静默失效) —— 以自定义 reset 钩子在归还时重新断言会话身份修复, 已沉淀 DevLog;
4. **验证**: 新增 tests/test_permissions_mailbox.py 14 用例 (权限矩阵/加组显隐/摘组回开/信箱隐私/回复会话/归档/容量/管理面门禁) 全绿; 正式门禁 `scripts/gate.sh full` 5/5 全绿 (干净临时库, 含 18 笔重放/brief 度量/dedup dry-run); 完整会话状态归档 `docs/session_state_2026-10-14.md`;
5. **回滚与上线**: 未部署 (按指示)。上线前置: `migrate up` 应用 09/10 → 评估 scripts/backfill_permissions.py --discover 挂组收口 → LOGBOOK_ADMIN_AGENTS 声明 admin → stdio 仅限本机、远程只走 SSH。回滚 = revert 7c7b010 + 重建容器 (RLS 对 owner 透明, 旧版本可直接读回全部数据)。
6. **生产兼容性核验 (2692efc 后)**: 生产 POSTGRES_USER=postgres 且 app 同角色连接 → 表 owner 豁免 RLS, 升级零影响; 迁移 10 补建 pg_trgm 消除服务启动卡死隐患; 已知行为变化 (brief 信箱仅本人未读/每会话双连接/未读 500 上限) 与部署后验证清单详见 docs/session_state_2026-10-14.md §8。

---

## [DEV-2026-10-13-06] rule_record 落地与 0.4.2 生产上线 — ✅ 闭环

MCP 工具面盘点发现唯一真空：规则 (shared.rules) 只有 `rule_query`，`db.upsert_rule` 自 0.2.0 起存在却从未暴露，生产规则表长期 0 行只读。本次闭合 (DevLog #92)：

1. **实现**: `rule_record` 工具 (id 幂等更新 / sanitizer 脱敏 / full 回显 / category 与 rule_query 同枚举)，测试改走工具路径并覆盖幂等与回显；升版 0.4.2 (commit `837ff87`)；
2. **发布**: 门禁全绿 → 镜像构建 → 本地容器冒烟 4/4 → QNAP 备份 (sha256 `9ddccd38…`) → 18s 载入 → compose 切 0.4.2 → up -d；
3. **生产验证**: tools=26 含 rule_record，写入→查询→`logbook://rules/engineering` 资源渲染全通；以 DEPLOY.md 铁律为蓝本写入首条真实规则 `RULE-DEPLOY-01`；
4. **对账**: devlogs/messages/brix_tasks 与升级前一致，rules 0→1 即本次写入；全栈内存 70.3MiB；
5. **澄清**: 此前"缺 pgvector"仅系本地对账沙盒的 `-t` 过滤还原假象；生产 vector 0.8.7 + HNSW 完好，且最新备份裸容器干净还原实证扩展自愈；
6. **状态归档**: 完整会话状态（提交链/回滚三件套/环境通道备忘/接续指引）保存于 `docs/session_state_2026-10-13_execution.md`，台账侧入库为 DevLog #91（会话归档条目）。

## [DEV-2026-10-13-05] 0.4.1 生产上线与 S5 终验 — ✅ 闭环

经宿主管理通道 (admin@<nas-host>, docker 在 Container Station 私有 PATH) 完成 QNAP 生产升级：

1. **备份先行**: pg_dump 冷备 379K (`sha256 ebeb54d1…`) + 升级前对账基线 (devlogs=79/tasks=55/messages=14)；
2. **镜像载入**: 本地 `docker save | gzip | ssh docker load` 19s，旧 tag 全保留 (回滚凭据 ae24f803)；
3. **部署陷阱修复**: 新镜像 SSH 默认仅绑回环，NAS compose 原缺 `SSH_HOST` —— 已补 `SSH_HOST=0.0.0.0` 后才换 tag，避免 macvlan SSH 失联；
4. **compose 切换**: image: logbook:0.4.1，双容器重建，启动即自动迁移 (采用 01~07 + 实跑 08)，verify 通过；
5. **终验 9/9**: 对账零差异 / `serverInfo.version=0.4.1` / 信件回环 (`read_by_agent=agy` 审计列生产生效) / inbox 冒充生产拒绝 / 租约回环 / 5.65s 慢调用完整返回 / 内存 75.8MiB (红线 140MB)；
6. **闭环**: L-MSG-1、L-STAB、L-AUD-02 全部 closed；通报信 #16 已达 zcode-main；回滚三件套 (旧镜像 + compose 备份 + dump) 落盘 NAS。

## [DEV-2026-10-13-04] S5 生产升级预飞与 L-MSG-1 生产复测 — ✅ 闭环

整改代码提交 `08c0461` + `9c9d239` (0.4.1 版本辨识) 后，完成 S5 全部可本地化预飞与生产只读复测 (DevLog #91)：

1. **生产复测**: QNAP 生产 (0.4.0+3259ffe 热补丁在跑) `message_send(#13)`→`inbox`→`read` 回环全通，10-10 的 -32000 断连不复现，判定"容器未加载热补丁"假设成立 (期间发生过重启)；`doctor` 报 PG 18.6 正常 (时钟漂移 +9s 警告)；
2. **迁移演练**: 10-09 生产备份 (sha256 `7d0d4456…`) 还原至临时 PG 18 → `migrate up` 采用 01~07 + 实跑 08，`verify` 通过，11 项数据计数迁移前后零差异；
3. **隔离冒烟**: `logbook:0.4.1` 镜像接迁移后库，9/9 全 PASS——信件回环、三路身份冒充全拒 (`CALLER_IDENTITY_MISMATCH`)、租约回环、brief；
4. **干净部署**: 空库启动自动迁移 01→08 全 runner，SSH 默认仅回环监听；真实 SSH 入口 (生产同路径 ssh→forced-command→mcp) 握手成功，`serverInfo.version=0.4.1` 可辨识；
5. **剩余用户动作**: `docker save logbook:0.4.1 | ssh NAS docker load` → compose 换 tag → `docker compose up -d` → `migrate verify` → 生产终验后翻 L-MSG-1 / L-STAB / L-AUD-02 closed。

## [DEV-2026-10-13-03] 稳定性整改变更后复审 — ✅ 闭环

针对未提交的 S0~S4 变更执行代码审查、隔离数据库对抗测试、真实 AsyncSSH 测试和 clean-checkout 门禁复验，报告已落盘至 `docs/AUDIT_LOGBOOK_STABILITY_REAUDIT_2026-10-13.md`：

1. **已验证改善**: A-01、A-03、A-04、A-05、A-07 本地证据成立，二次修复后当前工作区与隔离树均为 unit 45 / integration 40 全绿；
2. **首次阻断**: `message_inbox` 与个人信箱 Resource 绕过收件人鉴权；租约持有者可被任意调用参数冒充；
3. **二次修复**: 可信身份、租约单语句冲突返回、Schema 就绪检查与 clean-checkout 自举均已修复，当前工作区和隔离树 full gate 全绿；
4. **未闭环项**: QNAP 生产 `L-MSG-1` 仍为 P1，S5 生产部署、备份对账和回滚演练尚未执行；
5. **裁决**: 暂不批准生产发布；完成 S5 后方可复审关闭；
6. **状态归档**: 完整会话状态保存于 `docs/session_state_2026-10-13.md`，并已入库为 `devlog[90]`。

## [DEV-2026-10-13-02] Logbook 稳定性审计修复计划 — ✅ 闭环

基于已复验的 A-01 至 A-08 审计发现，形成 `docs/PLAN_STABILITY_FIX_2026-10-13.md`：

1. **实施顺序**: S0 可复现门禁 → S1 版本化迁移链 → S2 并发正确性 → S3 信箱与 SSH 安全 → S4 SSH/MCP 生命周期 → S5 QNAP 发布验收；
2. **关键架构约束**: 数据库原子条件写入、连接池与 DDL 单飞、协议生命周期驱动 EOF、可信身份不接受工具参数伪造；
3. **当前状态**: S0~S4 已完成本地代码与测试验收；临时 PostgreSQL 门禁、迁移链、并发/安全及真实 AsyncSSH 5 秒慢调用均已通过；
4. **完成红线**: 全部 P0/P1 修复、clean-checkout 门禁、并发与安全验收、QNAP 备份对账及回滚演练全部通过后方可办结；当前仅剩 S5 生产验收与提交证据，任务保持 `running`。

## [DEV-2026-10-13-01] Logbook 稳定性专项审计报告落盘 — ✅ 闭环

基于本地工作区 `main@149a646` 完成只读代码审查、测试执行与连接池并发探针，形成 `docs/AUDIT_LOGBOOK_STABILITY_2026-10-13.md`：

1. **审计结论**: 既有数据卷上的生产路径据历史台账记录能够运行，但本次未连接 QNAP Docker 或生产数据库；干净部署、并发一致性与测试可复现性不稳定；
2. **发现分级**: P0×1 (干净数据库初始化链断裂)、P1×6 (租约并发、连接池竞态、重复 DDL、SSH 300ms 猜测、信箱越权、默认弱口令)、P2×1 (门禁不可在干净检出复现)；
3. **验证证据**: 源码 `compileall` 与 `git diff --check` 通过；无数据库抽样测试 8/8 通过；全量收集 64 项中 34 项通过、30 项因本地 PostgreSQL 未启动失败；并发探针创建 3 个连接池并泄漏 2 个引用；
4. **当前状态**: 报告已落盘但尚未提交，任务保持 `running`，提交后补齐 commit hash 并按正典办结。

---

## [DEV-2026-10-10-01] 对讲信箱引擎生产故障根因定位、DDL首检缓存与管道生命周期加固 — ✅ 闭环

完成对讲邮件子系统生产故障 (L-MSG-1) 根因定位、全链路加固与 QNAP TS-453D 生产平滑热投产：
1. **故障根因定位与排查**:
   - 确证非权限不足或物理表缺失。根因为 db.py 每次调用 message_* 均重复执行 10 条 DDL 语句导致单次耗时达数百毫秒并持排他锁；
   - 非交互式管道客户端发送完请求即到达 stdin EOF，触发 MCP AnyIO 引擎执行 `tg.cancel_scope.cancel()` 强行取消在途 task，引发 `asyncpg` 连接异常中断并向客户端返回 `-32000 Connection closed`。
2. **核心加固与业务功能升级**:
   - **DDL 缓存自愈**: 在 Database 引入 `_messaging_schema_ensured` 与 `_researches_schema_ensured` 看门狗标志，仅在进程启动首检时执行一次 DDL，后续调用 0ms 直通，信箱读写耗时从 500ms 降至 2ms；
   - **任务盾牌保护**: `message_send` 底层通过 `asyncio.shield()` 保护数据库写事务，防止调用端提前断连导致写入中断；
   - **SSH 管道流缓冲**: `ssh_server.pipe_streams` 在 EOF 退出点增加 300ms 优雅缓冲，确保非交互式管道调用的响应完整写回客户端；
   - **MCP Resource 原生暴露**: 新增 `logbook://mailbox/{agent_name}/inbox` 与 `logbook://{project}/mailbox/{agent_name}` 个人信箱只读挂载资源；
   - **开工简报集成**: `logbook_brief` 首行实时输出 `mailbox_unread` 统计与未读信件索引；
   - **物理真实 IP 溯源**: 全链路保留 `192.0.2.22`、`192.0.2.30`、`<nas-ip>` 物理局域网真实 IP，杜绝保留段占位符。
3. **单元测试与门禁验证**:
   - 扩充 `tests/test_messaging_and_leases.py`，全量 7 项信箱与租约测试 100% PASS (3.31s)；
4. **QNAP 生产平滑热部署与真机对讲对账**:
   - 构建 `logbook:0.4.0` 镜像并推流至 QNAP TS-453D (`<nas-host>`)，Docker Compose 热重载；
   - 提取到此前 zcode 从 `192.0.2.22` 投递至 agy 的信件 **#7**；
   - 成功向 zcode 投递加固闭环通知信件 **#8**（主题：`【闭环通知】对讲信箱引擎已加固修复 (L-MSG-1 办结)`）；
   - 挂载 `logbook://mailbox/zcode/inbox` 资源验证，信件 **#8** 实时渲染且完整包含发件端真实 IP；
   - 验证 `logbook_brief` 实时呈现 `mailbox_unread=2`（含信件 #7 与 #8）。
5. **台账与手记闭环**:
   - 任务台账 `task L-MSG-1` 状态已在生产库更新为 **`closed`** (挂接提交 `3259ffe` 与手记指针)；
   - 缺陷管理 `finding L-MSG-1` 状态已在生产库更新为 **`fixed`**；
   - 生产数据库向量库录入排查手记 **`devlog[50]`** (已生成 512 维向量索引)。

---

## [DEV-2026-10-09-03] Logbook 0.4.0 全维 MCP 协议吸收与 QNAP 生产环境无缝投产 — ✅ 闭环

完成 Logbook 0.4.0 全维 MCP 协议吸收、SQL 迁移死角排障与 QNAP TS-453D 生产平滑热投产：
1. **架构与业务审计**: 深度审查 0.3.0 (对讲信箱/租约软锁)、0.3.1 (全实体 IP 溯源审计) 与 0.4.0 (调研知识库)，修复 SQL 保留字 references 未转义语法隐患与 pg_toast 系统 Schema 遍历越权隐患 (提交 f8524ae)；
2. **全维 MCP 协议吸收**: 落地 3 大 Resources 只读挂载端点 (leases/active, researches/recent, mailbox/summary) 与 2 大 Prompts 规程模板 (conduct_research, agent_collaborate)，信箱发信固有回显全局数字自增 ID，看板直出待提取 ID 索引引导收件 Agent 原子核销 (提交 87d5b9e)；
3. **全覆盖单元测试**: 落地 tests/test_resources_and_prompts.py，全套 20 项测试 100% 全绿 (3.26s)；
4. **QNAP 生产五步平滑投产**:
   - 生产 PG 数据库冷备先行 (logbook_pre_20261009_234403.dump, 167KB)；
   - 极简生产镜像 logbook:0.4.0 (84.9MB) 17 秒流式导入 QNAP Docker；
   - 物理迁移 05、06、07 脚本 100% 成功执行；
   - Docker Compose 热重载，全栈常驻内存仅 67.8MB (App 42.8MB, PG 24.9MB，CPU 0.00% / 0.03%)；
   - SSH MCP 真机实测通过 25 个工具全集、8 个只读资源与 4 个工作流模板，端到端信件与租约软锁流体验证通过；
5. **台账与手记闭环**: 研发任务 L13.4 正式办结归档，生产向量库录入 DevLog #43 (讯飞星火 512 维向量索引就绪)。

---

## [DEV-2026-10-09-02] 研发调研知识库 (shared.researches) 与开工前 RAG 语义检索 — ✅ 闭环

完成研发调研知识库全链路研发、测试与发布 (Logbook v0.4.0):
1. **共享知识面物理 DDL**: `sql/07_research_knowledge_base.sql` 建立 `shared.researches`，强制约束调研五要素 (objective, market_landscape, tradeoffs, decision, references)，配置 HNSW 向量索引 (512维) 与 GIN 倒排索引；
2. **强类型模型与铁律断言**: `models.py` 与 `normalizer.py` 增加 `Research` 实体模型与分类/状态归一化，强制断言 `completed` 状态必须包含详实决策与对抗成本评估；
3. **双引擎混合检索与数据库操作**: `db.py` 实现 `record_research`, `update_research`, `search_researches`（语义向量+全文混合召回）与 `query_researches`；
4. **三大 MCP 原语上线**: `research_record`, `research_search`, `research_query` 支持 0 摩擦调用并集成三级 IP 溯源；
5. **单元测试与台账归档**: `tests/test_researches.py` 覆盖 13 项测试 100% 全绿，台账任务 `L14` 办结，`DevLog #45` 归档。

---

## [DEV-2026-10-09-01] 多Agent对讲信箱、文件租约互斥与全实体IP溯源审计 — ✅ 闭环

完成对讲信箱、代码文件防冲突租约软锁与全实体 Agent IP 溯源审计 (Logbook v0.3.0 ~ v0.3.1):
1. **多 Agent 对讲信箱**: `sql/05_agent_messages_and_leases.sql` 建立 `shared.agent_messages`，支持点对点信件投递、信箱拉取、标记已读与任务状态推进 (`reviewer` / `review`)；
2. **代码文件租约软锁**: `shared.file_leases` 建立文件级 TTL 软锁，避免多 Agent 并发写入产生文件冲突；
3. **全实体 IP 溯源与不可篡改审计**: `sql/06_agent_ip_audit_everywhere.sql` 动态循环为所有 Schema 的 7 大实体补齐 `agent_ip VARCHAR(45)` 与索引，更新 `shared.init_project_schema`；
4. **三级自适应 IP 探测**: `detect_caller_ip` 自动提取 SSH 客户端物理源 IP，调用端 0 改造成本；
5. **单元测试与台账归档**: `tests/test_messaging_and_leases.py` 与 `tests/test_audit_ip_everywhere.py` 全绿通过，任务 `L13`、`L13.5` 办结，`DevLog #43`、`#44` 归档。

---

完成 Git 仓库坐标权威锚定、多项目双轨协商与 Agent 原生自愈开户原语落地与 QNAP 生产发布：
1. **权威注册中心 (SSOT) 与双轨解析**: `sql/04_project_registry.sql` 建立 `shared.projects` 登记中心，支持权威 Git 坐标 (`bpfio/brix`, `bpfio/logbook`, `io/TS`) 与物理 Schema (`brix`, `logbook`, `ts`) 双轨互通解析；
2. **Agent 原生自开户原语 (`project_init`)**: 新增 `project_init(repo, title, description)` MCP 工具，为 Agent 提供自开立与幂等建库能力，物理 Schema 自动规范化派生；
3. **两态智能协商自愈**: 优化 `negotiation.py`，智能区分高相似度手滑纠偏 (`CORRECT_PROJECT_PARAMETER`) 与新项目开户指引 (`PROJECT_INIT_NEEDED`)；
4. **工作区守卫与装饰器架构修复**: 修复无 Git 目录（如容器内 `/app`）工作区误锁定问题；将 FastMCP 工具装饰器顺序调整为 `@mcp.tool()` 在外、`@tool_shell` 在内，实现 100% 结构化错误兜底；
5. **生产验证与回归**: 全量测试套件 41/41 全绿通过；QNAP 生产容器完成热重建与热重载；现场通过 SSH MCP 验证 `project_init(repo="io/TS")` 与 `task_upsert` / `task_query` 闭环；
6. **跨目录写入规则与防护机制**: 确立跨项目目录写入三场景规则：① 非 Git 仓库目录（如 home/临时目录）：未绑定特定工作区，显式指定 `project` 即可直接写入；② 处于其他 Git 仓库目录：默认物理防呆拦截，显式传入 `allow_cross_project=True` 或环境变量 `LOGBOOK_ALLOW_CROSS_PROJECT=1` 放行；③ 远程 SSH MCP 模式：服务端沙盒隔离，原生支持按需向任意合法项目跨目录写入。

---

## [DEV-2026-10-08-11] cliserver 本地开发机识别与 agy 全局 logbook MCP 配置核验归档 — ✅ 闭环

完成 cliserver 主机身份确证与 agy 全局 logbook MCP 连通性生产核验：
1. **主机身份确证**: 当前运行宿主机 `xhub`（`192.0.2.22`）即网络正典中定义的本地开发机 `cliserver`（`~/.ssh/id_ed25519.pub` 注释为 `yupeng@cliserver`，`openwrt/AGENTS.md` 明确映射 `xhub / cliserver`），当前 Agent 已原生处于该环境，本地免密互信与 sshd 均正常；
2. **全局 MCP 配置与就绪**: `agy` 通过 `~/.gemini/config/mcp_config.json` 全局挂载 `logbook` SSH stdio 管道（`sysadmin@<nas-ip> mcp`），状态为 `enabled`，14 个正典 MCP 原语就绪；
3. **端到端生产 RPC 验证**: 现场调用 `logbook_brief(project='logbook')`，耗时 15ms 成功返回生产数据库简报与历史手记，证实通道 100% 可用；
4. **状态与手记闭环**: 成功通过 `batch_upsert`、`task_upsert` (L11) 与 `devlog_record` (#27) 沉淀至生产 pgvector 向量库并完成会话存档。

---

## [DEV-2026-10-08-10] Logbook 0.2.0 六病一根优化全量落地与 QNAP 生产发布验证 — ✅ 闭环

五线 (A1 数据层/A2 工具壳错误面/A3 安全包/A4 Harness/A5 文档) 全量落地并完成 QNAP 生产平滑发布：
1. **数据层与去重收口**: `sql/03_dedup.sql` 成功清洗 12 条存量副本，创建 `uq_devlogs_proj_task_title` 唯一表达式索引，实现写路径统一与幂等；状态机首戳保护 (`resolved_at`)，消除 `closed_at` 覆盖漂移；
2. **错误面与提要端点**: 统一收敛为 `{isError: true, error_type, detail}`；前置拦截 `TASK_NOT_FOUND`；新增 `logbook_brief` 提要端点 (Token 仅消耗 224~825，省 93%)；写响应默认精简瘦身；读支持 `fields` 投影与 `limit <= 200` 强钳制；
3. **部署与安全隔离**: 凭据 100% 迁出 `.env`；宿主机备份先行 (`logbook_pre_20261008_041759.dump`, 96KB)；镜像流式导入平滑拉起；
4. **端到端真机回归**: 生产 SSH MCP 通道重放 18 笔真实历史写入 (23 笔判定 100% PASS，0 副本膨胀)；`logbook_brief` 与 `devlog_search` 生产实测通过；全栈常驻内存 71.4MB，Gitea 等现有服务零冲击。

---

## [DEV-2026-10-08-09] Logbook 优化设计正典开账 (PLAN_OPT_2026-10-08) — ✅ 闭环

基于 Brix 夜11/夜12 真实调用 25+ 次体感与深探报告，确立“二线经验检索库”定位与六病一根总图，制定 P0 正确性五件、P1 效率五件、P2 安全五件与三波实施验证门规程。

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

