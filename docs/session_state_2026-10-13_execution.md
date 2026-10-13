# Logbook 执行会话状态：S5 生产上线与 0.4.1/0.4.2 双轮发布（2026-10-13）

> 本文是 2026-10-13 执行会话的完整状态归档（最终版），供后续会话无缝接续。
> 前置文档：`docs/session_state_2026-10-13.md`（审计与复审会话）、`docs/DEVLOG.md`。
> 台账锚点：任务 L-STAB / L-MSG-1 / L-AUD-01 / L-AUD-02 / L15 全部 closed；批次 DEV-2026-10-13-04/05/06 closed；DevLog #91（部署+时钟治理滚动归档）、#92（rule_record）。

## 1. 会话定位与终态

- 起点：复审报告 R-01~R-06 已二次修复但未提交、S5 生产验收未执行的 `main@149a646` 工作区。
- 用户指令序列：执行审计整改闭环 → 直接部署 QNAP → 数据完整性核验 → 时钟治理 → 补 rule_record → 保存全部会话状态并同步 GitHub。
- 终态：`main@8436bf3` 已推送 `bpfio/logbook`，生产运行 `logbook:0.4.2`，全部 P1 任务办结，工作区 clean，三端时钟对齐。

## 2. 提交与版本链

| commit | 内容 |
|---|---|
| `08c0461` | S0~S4 稳定性整改全量（可信身份/迁移链/租约CTE/SSH生命周期/门禁，36 文件） |
| `9c9d239` | 0.4.1 + serverInfo.version 注入（生产构建可辨识） |
| `4215201` / `7aa663d` | DEVLOG 归档（含 rebase 后哈希锚点修正） |
| `afffb29` | 0.4.1 上线与 S5 终验归档（DEV-2026-10-13-05） |
| `837ff87` | rule_record MCP 写入原语（0.4.2） |
| `2b57052` | DEV-2026-10-13-06 归档 + L15 办结 |
| `48565eb` | 执行会话状态首版归档 |
| `02ddeb1` | 时钟治理拓扑归档 |

注意：`336647e`/`11af7e0`/`fd6ebb9` 为 rebase 前旧哈希，内容分别等同 `08c0461`/`9c9d239`/`4215201`，台账 DEVLOG.md 已统一锚定新哈希。

## 3. 生产部署终态（QNAP TS-453D）

- 生产版本：`logbook:0.4.2`（compose `image: logbook:0.4.2`，含 `SSH_HOST=0.0.0.0`）；`initialize` 返回 `version=0.4.2`，tools=26（含 rule_record）。
- 迁移：`public.schema_migrations` 01~08（01~07 adopted，08 runner）；`read_by_agent` 审计列生效。
- 验证记录：0.4.1 终验 9/9（信件回环/身份拒绝/租约/5.65s 慢调用/对账/内存）；0.4.2 回环（rule_record→rule_query→rules 资源）全通；首条铁律 `RULE-DEPLOY-01` 已入库。
- 回滚三件套：镜像 `logbook:0.4.1`(d63d65d75c81)/`logbook:0.4.0`(ae24f803) 保留；`compose.yaml.bak_0.4.1_*`；备份 `/share/backups/logbook_pre_0.4.1_20261013_1817.dump`(sha256 `ebeb54d1…`)、`logbook_pre_0.4.2_20261013_1845.dump`(sha256 `9ddccd38…`)。
- 其他容器零影响：gitea/fastapi-dls/kms 未被触碰；全栈内存 ~70MiB（红线 140MB）。

## 4. 数据完整性核验（深度口径）

- 全库 36 张表逐行计数：升级前备份 vs 生产当前，差异仅三处且全部为会话自身写入（messages +2 = 信 #15/#16；logbook.task_timeline +3 = 三项任务办结；`schema_migrations` 新表 8 行 = 迁移账本本体）。
- 内容级哈希：`brix.tasks` 55 行与升级前**逐字节一致**；`shared.devlogs` 79 行唯一差异为 #91（会话自身更新）。结论：**零数据丢失**。
- 方法论教训：跨机哈希比对必须在同一环境渲染（生产 PG 默认 +08 时区 vs 临时库 UTC 曾造成全行哈希假差异）；pg_restore `-t` 过滤会跳过 dump 内的 EXTENSION 条目。

## 5. pgvector 实证结论

- 生产：vector 0.8.7（public schema）+ `shared.devlogs.embedding` 512 维 + HNSW (cosine, m=16, ef=64) + GIN(tsv) 全部在线。
- 恢复链路：最新备份在裸 `pgvector/pgvector:pg18` 容器零手工干预还原 → 扩展自动重建、索引就位、余弦查询可跑。**此前"缺 pgvector"系本地对账沙盒 `-t` 过滤还原的假象，生产从未缺失。**

## 6. 时钟治理（2026-10-13 晚完成）

- 病根：QNAP `ntp.conf` 只有本地时钟驱动（Server1/2/3 全空回退），配置存储里的 `pool.ntp.org` 被局域网 DNS fake-ip（198.18.x）劫持永不可达；`ntpdated` 守护进程占 123 端口与 `ntpd.sh` 校验竞态进一步阻断。
- 修复：`setcfg` 写入 Server1=ntp.aliyun.com / Server2=ntp.tencent.com / Enable NTP Server=TRUE → 释放端口重新生成 → `ntpdate -b` 步进 -9.16s → ntpd 锁定上游并作为 LAN NTP 服务端。
- 本机：时区 Asia/Shanghai；chrony 选源死锁根因是 `china-ntp.sources` 与新建文件重复定义 aliyun/tencent（同主机名双载入选源永不收敛）——去重后经 QNAP 同步成功（Stratum 4，synchronized=yes，钟差纳秒级）。
- 终验：生产 doctor 时钟项 ✔（+0.010s，授时中心 ntp.aliyun.com）；三端秒级一致。
- 陷阱备忘：QTS 配置必须走 `/sbin/setcfg`（直改 uLinux.conf 会被守护进程缓存回写）；`pool.ntp.org` 本局域网禁用；chrony `sources.d` 严禁重复定义同一主机名。

## 7. 环境通道备忘（接续必读）

- 生产 MCP：`ssh sysadmin@192.168.1.68 mcp`（forced-command，仅 mcp/CLI；`logbook doctor`/`logbook status` 可用）。
- QNAP 宿主：`192.168.1.33`，docker 在 `/share/CACHEDEV1_DATA/.qpkg/container-station/bin`（需 export PATH）；部署目录 `/share/CACHEDEV1_DATA/Container/logbook`；宿主凭据不在仓库内（通道机制见 `tests/benchmark_live.py` 的 QNAP_PASSWORD 用法）。
- 部署陷阱：新镜像 SSH 默认仅绑回环，**compose 必须显式 `SSH_HOST=0.0.0.0`**，否则 macvlan SSH 失联。
- 本会话 MCP 工具清单为会话建立时快照（25 工具），`rule_record` 需客户端重连后可见（生产侧已验证可用）。

## 8. 生产健康度评估与遗留项（按优先级）

评估结论：**production-ready 但非"完美"**。核心功能/安全/数据完整性全部实测达标；距离"完美"差以下运维基本功：

1. 备份未自动化且不出 NAS（两份 dump 同机存放）——建议定时 pg_dump + 异机副本；
2. 无监控告警——建议 cron 巡检 doctor + SMTP 通知；
3. ~~时钟漂移 +9s~~ **已解决**（见第 6 节）；
4. NAS admin 密码认证仍开启且密码存在于用户侧历史文件——建议改公钥管理并轮换；
5. 单点架构（TS-453D 一机承担计算+存储）——硬件形态约束，非缺陷；
6. MCP 协议提升项：structuredContent/outputSchema、资源分页、信箱广播原语；
7. zcode-main 复测回执未回（信 #14/#16 在其信箱）；`L13` 主任务保持 planned，属用户规划项。

## 9. 接续指引

新会话开工：先 `logbook_brief(project=logbook)` → 通读 `docs/DEVLOG.md` 顶部 → 按需回看本文第 7 节通道备忘与第 8 节遗留项。生产不可用时回滚命令见 `deploy/qnap/DEPLOY.md` 第 6 节，回滚凭据见本文第 3 节。

本执行会话的 ZCode 会话 ID：`sess_21a018d8-3afa-4596-af4a-7b129929c9a3`——需要完整对话上下文（决策链、误判证伪过程、命令细节）时，可用 `ReadSessionContext(sessionId="sess_21a018d8-3afa-4596-af4a-7b129929c9a3", strategy="handoff")` 直接拉取。
