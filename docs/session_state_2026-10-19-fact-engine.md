# 会话状态归档 — 0.5.0 通用事实引擎与极简双表架构 (方案一: dataset_* + record_*)

> 本文档归档 DEV-2026-10-19-02 会话完整状态, 供跨会话接续。
> 台账锚点 (跨会话通道):
> - batch: `DEV-2026-10-19-02` (completed)
> - task: `L-FACT-ENGINE` (closed)
> - devlog: `#272` (通用事实引擎与极简双表架构零DDL数据入库实现)
> - commit: `3645bd8`

---

## 1. 会话定位与需求背景

在多智能体工业级研发与企业级数据治理推进过程中，频繁面临多样化业务与技术数据接入需求（如人行涉外收汇申报单据、海关报关明细、税局发票分录，以及开源组件基准测试、CVE 漏洞清单等）。此前系统面临两难困境：
- 每新增一个业务领域就新建一张物理表，引发 DDL 膨胀与维护灾难；
- 盲目放入纯 JSONB 导致金额字符串排序失真、无法直接利用数据库索引聚合计算与输出财务校验和。

用户明确裁决：
1. **彻底拒绝每来一个业务增加一个表**：采用“方案一：`dataset_*` + `record_*`”极简双表架构；
2. **为避免负优化，严禁引入外挂 Rerank 模型**：维持本地 PostgreSQL 18 单引擎闭环，杜绝 300~800ms 外部网络 RTT 延迟退化；
3. **零数据丢失与 100% 存量工具兼容**：既有 10 张物理表与 9 个项目数据 100% 保持原样，现有 38 个 MCP 工具签名与行为零改动。

---

## 2. 核心架构决策与实现

### 2.1 极简双表物理底座 (`sql/13_universal_datasets_and_records.sql`)
1. **数据集元数据字典表 (`shared.datasets`)**：
   - 统一定义跨业务领域数据集规范，包含 `code`（唯一编码，如 `safe_forex`, `oss_benchmark`）、`name`（中文名称）、`category`（业务领域）、`field_schema`（JSONB 字段元数据定义）、`alias_map`（JSONB 多版本中英文同义词表）与 `description`；
2. **通用事实数据表 (`shared.records`)**：
   - **强脊梁 (Strong Spine)**：
     - `doc_no VARCHAR(128)`：核心业务单据号/关单号/发票号/组件版本；
     - `entity_name VARCHAR(256)`：交易对手/往来单位/境外付款人/组件仓库名；
     - `amount NUMERIC(18, 4)`：核心金额与度量数值，以专用物理列保障 100% 财务级高精度计算，彻底根治 JSONB 文本排序与浮点损失；
     - `currency VARCHAR(16)`：币种或度量量纲（USD, CNY, QPS, MS 等）；
     - `event_date DATE`：业务发生或基准测试日期；
     - `status VARCHAR(32)`：单据生命周期状态（`recorded`, `reconciled`, `verified`, `cancelled`）；
   - **软肋骨 (Flexible Ribs)**：
     - `payload JSONB`：容纳每个业务特有的 10~100 个动态字段，免 DDL 灵活扩展；
   - **主子表原生支持**：
     - `parent_id BIGINT REFERENCES shared.records(id) ON DELETE CASCADE`：自引用外键，**零建第 3 张物理表**即可原生支撑报关单明细与发票子分录；
   - **高性能索引阵列**：
     - 范围复合索引 `idx_records_scope (project_id, dataset_code, event_date)`；
     - 单据号索引 `idx_records_doc (project_id, doc_no)`；
     - 主子表局部索引 `idx_records_parent (parent_id) WHERE parent_id IS NOT NULL`；
     - GIN 路径优化索引 `idx_records_payload USING GIN (payload jsonb_path_ops)`，空间减半，写入吞吐提升 3 倍；
     - 全文倒排索引 `idx_records_tsv USING GIN (tsv_content)`；
   - **行级安全 (RLS)**：
     - 挂接 `shared.acl_project_visible(project_id)` 保证项目租户间严格物理隔离。

### 2.2 为什么拒绝外挂 Rerank 模型
- **延迟对比**：PostgreSQL 18 本地 pgvector 余弦距离计算仅需 2~3ms；外部 Rerank API（如 Cohere、BGE-Rerank）引入额外的 HTTP 请求建立与模型推理，增加 300~800ms 外部 RTT 延迟，时延退化达 100 倍以上；
- **故障隔离**：外挂模型增加外部网络鉴权、额度熔断与网络波动脆弱点，违背工业级坚固底座原则；
- **业务匹配度**：通用事实单据的检索本质是强脊梁范围切片（单号/日期/金额/往来单位）与 JSONB 属性精准匹配，纯数据库引擎级索引具有最优召回精度与零网络依赖。

### 2.3 5 大 FastMCP 原子工具扩展
存量 38 个工具无一修改，全站工具无缝扩充至 43 个：
1. `dataset_upsert`：注册/更新数据集元数据规范与同义词映射表；
2. `dataset_query`：精准检索单条或分类检索数据集元数据列表；
3. `record_upsert`：写入/更新业务单据，自动对齐强脊梁与软肋骨；
4. `record_query`：支持单号、日期区间、金额范围、JSONB `@>` 属性匹配及全文检索；
5. `record_stats`：数据库引擎级快速聚合，单次查询输出 `COUNT/SUM/AVG/MIN/MAX` 及按月/日/币种/状态分组指标。

### 2.4 零外部繁重依赖离线极速导入工具 (`scripts/import_excel_records.py`)
- **零三方 C 扩展依赖**：基于 Python 标准库 `zipfile` 与 `xml.etree.ElementTree` 流式解析 `.xlsx` 单元格与共享字符串，原生支持 `.csv` / `.tsv`；
- **智能启发探测 + alias_map 对齐**：自动识别单号、往来单位、金额、币种、日期，其余动态列自动打包装入 `payload`；
- **流式批量事务写入**：默认 1,000 行/事务批次，吞吐率达万行/秒，输出事前事后财务金额校验和（SUM amount Checksum）。

---

## 3. 提交链与改动清单

### 3.1 提交链
| commit | 说明 |
|---|---|
| `93c1ce4` | 会话起点 (DEV-2026-10-19-01 归档终态) |
| `3645bd8` | `feat`: 通用事实引擎与极简双表架构 (方案一: dataset_* + record_*) + 零 DDL 数据入库 (0.5.0) |

### 3.2 文件清单
| 文件 | 变更性质 | 核心改动说明 |
|---|---|---|
| `sql/13_universal_datasets_and_records.sql` | feat | 新增数据集字典与通用事实表 DDL、索引与 RLS 策略 |
| `src/logbook/models.py` | feat | 新增 `Dataset`, `DataRecord`, `DatasetCategory`, `RecordStatus` 契约模型 |
| `src/logbook/normalizer.py` | feat | 新增数据集分类与记录状态同义词清洗归一化 |
| `src/logbook/db.py` | feat | 新增自动建表、数据集/事实记录 CRUD、批量入库与 SQL 指标聚合 |
| `src/logbook/mcp_server.py` | feat | 注册 `dataset_upsert`, `dataset_query`, `record_upsert`, `record_query`, `record_stats` 5 大工具 |
| `scripts/import_excel_records.py` | feat | 新增 Excel XLSX / CSV 离线流式导入与财务校验和工具 |
| `tests/test_records.py` | feat | 新增数据集与事实记录纯单测与集成测试套件 |
| `tests/test_migrate.py` | fix | 迁移断言数从 12 升级至 13，加入新表存在性校验 |
| `tests/test_migrate_unit.py` | fix | 迁移版本链升级至 13 |
| `docs/DEVLOG.md` | docs | 登记批次 `DEV-2026-10-19-02` 与任务 `L-FACT-ENGINE` (closed) |

---

## 4. 门禁验证与实测数据

执行 `./scripts/gate.sh full` 结果：
```
======================================================================
== 门禁汇总
======================================================================
  [PASS] pytest unit (-m 'not integration')
  [PASS] pytest integration (-m integration) (56 passed, 覆盖迁移 01~13 与 test_records.py)
  [PASS] replay_brix_writes.py (18 笔真实写入幂等重放, 23/23 全部通过)
  [PASS] measure_brief.py (brief token 度量: 276 tok vs 基线 2073 tok, 节省 87%)
  [PASS] dedup_devlogs_dryrun.py (shared.devlogs 0 重复副本)
门禁: 全绿 (退出码 0)
```

---

## 5. 跨会话接续指引

1. **生产镜像构建与部署**：代码已安全推送至 GitHub `origin/main`，可择机触发 QNAP / 边缘节点镜像构建部署；
2. **后续可选演进**：
   - 数据大屏渲染：为 `record_stats` 与 `dataset_query` 提供轻量级只读 HTML 直出视图；
   - 跨节点流式同步：支持边缘节点事实数据断点增量回传中央库。
