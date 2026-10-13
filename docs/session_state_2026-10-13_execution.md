# Logbook 执行会话状态：S5 生产上线与 0.4.1/0.4.2 双轮发布（2026-10-13）

> 本文是 2026-10-13 执行会话的完整状态归档，供后续会话无缝接续。
> 前置文档：`docs/session_state_2026-10-13.md`（审计与复审会话）、`docs/DEVLOG.md`。
> 台账锚点：任务 L-STAB / L-MSG-1 / L-AUD-01 / L-AUD-02 / L15 全部 closed；批次 DEV-2026-10-13-04/05/06 closed；DevLog #91/#92。

## 1. 会话定位

- 起点：复审报告 R-01~R-06 已二次修复但未提交、S5 生产验收未执行的 `main@149a646` 工作区。
- 用户指令序列：执行审计整改闭环 → 直接部署 QNAP → 补 rule_record → 保持全部会话状态并同步 GitHub。
- 终态：`main@2b57052` 已推送 `bpfio/logbook`，生产运行 `logbook:0.4.2`，全部 P1 任务办结，工作区 clean。

## 2. 提交与版本链

| commit | 内容 |
|---|---|
| `08c0461` | S0~S4 稳定性整改全量（可信身份/迁移链/租约CTE/SSH生命周期/门禁，36 文件） |
| `9c9d239` | 0.4.1 + serverInfo.version 注入（生产构建可辨识） |
| `4215201` / `7aa663d` | DEVLOG 归档（含 rebase 后哈希锚点修正） |
| `afffb29` | 0.4.1 上线与 S5 终验归档（DEV-2026-10-13-05） |
| `837ff87` | rule_record MCP 写入原语（0.4.2） |
| `2b57052` | DEV-2026-10-13-06 归档 + L15 办结 |

注意：`336647e`/`11af7e0`/`fd6ebb9` 为 rebase 前旧哈希，内容分别等同 `08c0461`/`9c9d239`/`4215201`，台账 DEVLOG.md 已统一锚定新哈希。

## 3. 生产部署终态（QNAP TS-453D）

- 生产版本：`logbook:0.4.2`（compose `image: logbook:0.4.2`，含 `SSH_HOST=0.0.0.0`）；`initialize` 返回 `version=0.4.2`，tools=26（含 rule_record）。
- 迁移：`public.schema_migrations` 01~08（01~07 adopted，08 runner）；`read_by_agent` 审计列生效。
- 生产验证记录：0.4.1 终验 9/9（信件回环/身份拒绝/租约/5.65s 慢调用/对账零差异/内存）；0.4.2 回环（rule_record→rule_query→rules 资源）全通。
- 回滚三件套：镜像 `logbook:0.4.1`(d63d65d75c81)/`logbook:0.4.0`(ae24f803) 保留；`compose.yaml.bak_0.4.1_*`；备份 `/share/backups/logbook_pre_0.4.1_20261013_1817.dump`(sha256 `ebeb54d1…`)、`logbook_pre_0.4.2_20261013_1845.dump`(sha256 `9ddccd38…`)。
- 数据完整性：升级前后全库 36 表逐行计数一致；`brix.tasks` 内容哈希逐字节一致；`shared.devlogs` 79 行唯一差异为 #91（会话自身更新）。三处增量（messages +2、timeline +3、schema_migrations 新表）均为会话自身写入或迁移产物。
- 其他容器零影响：gitea Up 10 天、fastapi-dls/kms Up 2 个月未重启；全栈内存 ~70MiB（红线 140MB）。

## 4. 环境通道备忘（接续必读）

- 生产 MCP：`ssh sysadmin@192.168.1.68 mcp`（forced-command，仅 mcp/CLI；`logbook doctor`/`logbook status` 可用）。
- QNAP 宿主：`192.168.1.33`，docker 在 `/share/CACHEDEV1_DATA/.qpkg/container-station/bin`（需 export PATH）；部署目录 `/share/CACHEDEV1_DATA/Container/logbook`；宿主凭据不在仓库内（通道机制见 `tests/benchmark_live.py` 的 QNAP_PASSWORD 用法）。
- 部署陷阱：新镜像 SSH 默认仅绑回环，**compose 必须显式 `SSH_HOST=0.0.0.0`**，否则 macvlan SSH 失联。
- pgvector 结论：生产 vector 0.8.7 + HNSW 完好；最新备份裸容器干净还原实证扩展自愈（此前"缺扩展"系本地对账沙盒 `-t` 过滤还原的假象）。
- 时钟治理（2026-10-13 晚已完成）：QNAP 已配置 ntp.aliyun.com/ntp.tencent.com 双上游并作为 LAN NTP 服务端（`Enable NTP Server=TRUE`，UDP 123 全接口监听），漂移 +9.16s 已步进消除，生产 doctor 时钟项 ✔；本机时区 Asia/Shanghai，chrony 经 QNAP 同步（Stratum 4，synchronized=yes）。注意：QTS 配置必须走 `/sbin/setcfg`（直改 uLinux.conf 会被守护进程缓存回写覆盖）；`pool.ntp.org` 在本局域网被 DNS fake-ip 劫持不可用，禁用；chrony 的 `sources.d` 各文件严禁重复定义同一主机名（会导致选源死锁）。详见 DevLog #91。
- 已知非阻断项：NAS admin 凭据目前仅存在于用户侧历史文件，建议改公钥管理。

## 5. 遗留与后续建议（均非阻断）

1. zcode-main 侧复测回执未回（信件 #14/#16 在其信箱）；brix 端抽测确认后无额外动作。
2. `L13`（0.3.0 里程碑主任务）保持 planned，属用户规划项，待用户裁决。
3. MCP 协议提升项：structuredContent/outputSchema 结构化输出、资源分页、信箱广播原语。
4. DEVLOG.md 中 L-MSG-1 行历史备注仍含旧口径（10-10 退回），已由新行覆盖，无行动项。

## 6. 接续指引

新会话开工：先 `logbook_brief(project=logbook)` → 通读 `docs/DEVLOG.md` 顶部 → 按需回看本文第 4 节通道备忘。生产不可用时回滚命令见 `deploy/qnap/DEPLOY.md` 第 6 节，回滚凭据见本文第 3 节。
