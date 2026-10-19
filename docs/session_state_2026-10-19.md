# 会话状态归档 — 0.5.0 收口 + 创作工程阶段一 + 政策知识库扩充 (方案B) & 768维向量升级 (2026-10-19)

> 本文档归档 DEV-2026-10-19-01 会话完整状态, 供跨会话接续。
> 台账锚点 (跨会话通道):
> - batch: `DEV-2026-10-19-01` (completed)
> - task: `L-CREATIVE-1` (closed) / `L-RESEARCH-768` (closed)
> - finding: `F-REL-01` (fixed) / `F-REL-02` (fixed)
> - devlog: `#271` (768维原生向量升维与通用知识库扩充)

---

## 1. 会话定位与需求背景

本会话承接三个递进业务目标：
1. **0.5.0 发版前置收口**：办结审查发现的 `F-REL-01`（版本号对齐）与 `F-REL-02`（部署环境示例文档）；
2. **长篇网络小说创作工程支撑（阶段一）**：针对百万字网络长篇小说创作（大纲拆解、细纲节拍、正文草稿、文风润色、伏笔/设定连贯性稽核）进行工程调研，落地任务类型扩充与闭环断言适配；
3. **政策法规研究通用知识库（方案B·单表复用）与 768 维向量升级**：贯彻用户裁决“增加数据结构丰富度，严禁随意多一个业务新造一个表”的单一事实源（SSOT）铁律，复用 `shared.researches` 底座，扩展政策/世界观核心元数据，并将全站嵌入向量平滑升维至原生 768 维。

---

## 2. 核心架构决策与实现

### 2.1 方案B：单表复用与零性能折损（TOAST机制）
- **架构哲学**：绝不因新增“政策研究”或“小说设定考据”创建独立业务表，而是通过在 `shared.researches` 上做横向正交字段扩展（`doc_no` 发文字号/设定编号、`authority` 制定主体/机构、`legal_level` 效力位阶/分类体系、`effective_date` 施行生效日期）；
- **存储与性能**：PostgreSQL 默认对超过 2KB 的变长字段（如大文本正文、向量）自动启用 TOAST 外部切片压缩存储，元数据行依然极度紧凑（单页可容纳数百行元数据），B-Tree 索引、GIN 全文检索与 HNSW 向量索引完全正交，单次查询 CPU 耗时维持在微秒级；
- **分类与状态扩充**：
  - `ResearchCategory` 扩充：`policy_macro`, `policy_industry`, `policy_tech`, `policy_fiscal`, `governance`, `lore`, `general`；
  - `ResearchStatus` 扩充：`in_force`, `draft`, `revised`, `repealed`；
  - 配合 `src/logbook/normalizer.py` 汉字别名与机构名自动归一化。

### 2.2 768 维原生向量平滑升维与兼容机制
- **选型动因**：主流中文基础向量模型（讯飞星火 Spark Embedding API、BAAI BGE-base、text2vec 等）均为原生 768 维度。此前 512 维采用 MRL（Matryoshka Representation Learning）有损截断；升至 768 维后彻底消除截断精度损失，匹配中文语义最优召回率；
- **平滑迁移 12**（`sql/12_upgrade_768_and_enrich_research.sql`）：
  - 由于 PostgreSQL pgvector 不支持将已有非空 `vector(512)` 直接 `ALTER COLUMN TYPE vector(768)`，迁移先将现有 `embedding` 列置为 NULL，原子修改列定义为 `vector(768)`，并重建 HNSW 余弦索引；
  - 将 4 个新增政策元数据列纳入 `tsv_content` 数据库生成列与 GIN 全文索引；
  - 嵌入置空期间，MCP 搜索自动由混合检索平滑退化至高精度全文检索，服务 0 报错；
- **离线重嵌工具**（`scripts/reembed_768.py`）：
  - 提供批处理离线回填脚本，断点续传重新填充 768 维向量；本地无网络测试环境同频降级至 768 维确定性哈希向量。

### 2.3 创作工程阶段一支持
- **任务类型扩充**：`outline`（大纲/细纲）, `draft`（正文起草）, `polish`（润色）, `continuity`（伏笔与一致性稽核）, `lore`（世界观考据）；
- **闭环断言解耦**：`validate_invariants` 与数据库 CHECK 约束适配：创作类任务允许以详实 `notes` 或 `commit_hash` 闭环，研发任务（feat/fix）的 strict `commit_hash` 约束保持不变；
- **平滑迁移 11**（`sql/11_creative_task_types.sql`）：动态更新项目初始化函数及存量项目 schema 的 CHECK 约束。

---

## 3. 提交链与改动清单

### 3.1 提交链
| commit | 说明 |
|---|---|
| `e7f3968` | 会话起点 (上一会话终态: 0.5.0 上线前审查归档) |
| `71c5619` | `feat`: 创作工程阶段一 + 通用知识库扩充 (方案B) & 768维向量平滑升级 (0.5.0) |

### 3.2 文件改动清单
| 文件 | 变更性质 | 核心改动说明 |
|---|---|---|
| `pyproject.toml` | fix | 版本号 bump 0.5.0 (`F-REL-01`) |
| `src/logbook/__init__.py` | fix | `__version__` 对齐 0.5.0 (`F-REL-01`) |
| `deploy/qnap/compose.yaml.example` | fix | 补齐 `LOGBOOK_ADMIN_AGENTS` 环境变量文档 (`F-REL-02`) |
| `deploy/qnap/.env.example` | fix | 补齐 `LOGBOOK_ADMIN_AGENTS` 说明与示例 (`F-REL-02`) |
| `src/logbook/models.py` | feat | TaskType 扩充创作类型；ResearchCategory/Status 扩充政策与设定类型；Research 增 4 元数据列 |
| `src/logbook/normalizer.py` | feat | 增加创作同义词与政策机构/状态汉字映射 |
| `src/logbook/vector.py` | feat | `EMBEDDING_DIM` 缺省升至 768；fallback hash 输出 768 维 |
| `src/logbook/db.py` | feat | 适配 `vector(768)` 与 researches 4 大新字段的写入、更新与过滤 |
| `src/logbook/mcp_server.py` | feat | research 3 大工具暴露新参数；更新工具说明 |
| `sql/11_creative_task_types.sql` | feat | 迁移 11：存量项目 CHECK 约束动态升级与 init_project_schema 更新 |
| `sql/12_upgrade_768_and_enrich_research.sql` | feat | 迁移 12：升维 vector(768)，重建 HNSW，扩充 researches 字段与 tsv_content |
| `scripts/reembed_768.py` | feat | 存量向量 768 维平滑回填脚本 |
| `tests/test_creative_tasks.py` | test | 创作任务类型与 notes 闭环断言 6 个单测 |
| `tests/test_researches.py` | test | 政策研究模型校验与 768 维向量生成单测 |
| `tests/test_logbook.py` | test | 适配 768 维向量断言 |
| `tests/test_migrate.py` / `test_migrate_unit.py` | test | 适配 12 步迁移链断言 |
| `docs/DEVLOG.md` | docs | 归档 L-CREATIVE-1、L-RESEARCH-768、F-REL-01、F-REL-02 与批次 DEV-2026-10-19-01 |

---

## 4. 门禁验证记录

运行 clean-checkout 完整门禁 `./scripts/gate.sh full`，**5/5 全绿（退出码 0）**：
1. **pytest unit**: 53 passed, 54 deselected (耗时 10.58s)；
2. **pytest integration**: 54 passed, 53 deselected（真实拉起隔离 PG18 容器，运行 01~12 全迁移链、768维向量持久化与余弦检索，耗时 23.44s）；
3. **replay_brix_writes.py**: 23 笔真实写入两轮幂等重放，23/23 全部通过（退出码 0）；
4. **measure_brief.py**: brief token=276 tok vs 基线 2073 tok（节省 87%，≤2000 tok 达标）；
5. **dedup_devlogs_dryrun.py**: 0 重复副本冲突，通过。

---

## 5. 台账归档状态

| 载体 | 记录内容 |
|---|---|
| `docs/DEVLOG.md` | 任务台账 L-CREATIVE-1 / L-RESEARCH-768 (closed, 28 条) + 发现台账 F-REL-01 / F-REL-02 (fixed, 8 条) + 批次 [DEV-2026-10-19-01] |
| logbook MCP batch | `DEV-2026-10-19-01` completed |
| logbook MCP task | `L-CREATIVE-1` closed / `L-RESEARCH-768` closed |
| logbook MCP finding | `F-REL-01` fixed / `F-REL-02` fixed |
| logbook MCP devlog | `#271` (768 维向量升维与通用知识库扩充方案B落地) |

---

## 6. 接续指引 (下一步动作)

1. **生产镜像构建与部署**：
   - 0.5.0 当前代码包含：多用户分组权限（09）、站内邮箱完成面（10）、创作任务支持（11）、政策知识库与768维升级（12）；
   - 上线时按 `docs/session_state_2026-10-14.md` §6/§8 执行备份、compose 切新 tag、容器自愈迁移；
   - 部署完成后运行 `python scripts/reembed_768.py` 填补 768 维向量；
2. **长篇网络小说创作工程阶段二（规划中）**：
   - 在阶段一 TaskType 与知识库扩充完备的基础上，后续可规划：
     - 写作工作区接入与长篇目录结构约定（卷/章/节）；
     - 自动上下文拼装与章节前置 RAG 检索（调取世界观设定、人物关系谱与前置伏笔）；
     - 阶段二待办任务立项。
