# 会话状态归档 — Logbook 0.8.1 读写对称性、局部补丁模式、评审缺陷闭环与生产投产

> 本文档归档 `DEV-2026-11-14-01` 会话完整状态，供跨会话接续。
> 台账锚点 (跨会话单一事实源 SSOT):
> - batch: `DEV-2026-11-14-01` (completed / closed)
> - task: `L-OPT-081` (closed)
> - devlog: `[DEV-2026-11-14-01]` (Logbook 0.8.1: 读写对称性补齐、局部补丁模式、强脊柱保全与契约自解释增强)
> - commit: `b3fdc76` (fix(core): id 与 parent_id 采用 StrictInt 强类型声明杜绝 bool 隐式强转 (F-12))
> - remote: `origin/main` (https://github.com/bpfio/logbook.git)
> - image: `logbook:0.8.1` (生产 QNAP TS-453D 已上线并热重载生效)
> - records: 6,966 条生产事实记录 (100% 绝对零丢失)
> - gate: 80 passed, 40 deselected in 10.57s (100% 全绿)

---

## 1. 会话背景与需求闭环

本轮会话源自对 Agent 在日常使用 Logbook MCP 工具过程中遇到的四类结构性与契约痛点的主动复盘与用户裁决：

1. **读写严重不对称 (ISSUE-01)**：
   - 写侧 `record_upsert` 返回记录包含物理主键 `id: int`，且入参支持 `id`；但读侧 `record_query` 未开放 `id` 过滤，传 `id` 直接被 FastMCP 拦截为 `INVALID_ARGUMENT: id Extra inputs are not permitted`；
2. **整条替换导致静默丢失字段风险 (ISSUE-02)**：
   - `record_upsert` 历史实现为全量 JSONB 替换，修改轻微错别字或单独字段必须全文重传所有动态属性，存在极高的丢内容与漏传脆性风险；
3. **主键更新强绑定单据号 (ISSUE-03)**：
   - 历史声明中 `doc_no` 为必填参数，即使显式提供 `id` 仍强制要求 `doc_no`，导致未缓存单号时无法直接按 ID 更新；
4. **Schema 校验缺乏自解释引导 (ISSUE-04)**：
   - `validate_payload_schema` 校验异常仅报字段缺失，未提示可用必填项列表与资源入口，导致 Agent 靠猜或翻历史记录反推字段。

---

## 2. 演进与审计复核历程 (Four-Round Hardening)

### 2.1 第一轮：基础能力落地 (Commit: `f81f7d3`)
- `record_query` 增设 `id: int | list[int] | None = None` 与 `applied_filters["id"]` 回显；
- `record_upsert` 增设 `patch: bool = False` 参数，在更新已有单据时与旧 payload 执行合并并重跑 Schema 校验；
- `doc_no` 声明为可选，仅在新增单据时强制非空；
- `validate_payload_schema` 丰富抛错文案，输出必填项列表与自愈指引。

### 2.2 第二轮：外部独立深度审计整改 (F-1 ～ F-4，Commit: `d6f49ed` / `692d56e`)
用户对代码进行了高精度独立代码走查，指出关键设计缺陷：
- **F-1 (高危，脊柱覆盖)**：`patch=True` 仅保全了软肋骨（`payload`），但在 `update_sql` 中将 `amount/currency/event_date/status` 无条件赋值为 MCP 默认值，导致修改任务备注会把 `closed` 状态冲刷回 `recorded`，并将业务日期篡改为今天；
  - **修复**：穿透 Pydantic 的 `model_fields_set`，在 `patch=True` 模式下仅更新显式传入的字段，未传强脊柱字段严格从 `existing_row` 继承；
- **F-2 (中危，断言缺乏判别力)**：原单测断言 Mock 写死的返回值而非传递给数据库的真实绑定参数；
  - **修复**：重构单测，深入提取 `mock_conn.fetchrow.call_args_list[1]` 的真实 SQL 位置参数元组并逐一断言；
- **F-3 (中危，ID 校验 fail-open)**：`str.isdigit()` 会将 `"abc"`、负数、空数组静默丢弃导致穿透全表，且 `'²'` 等上标数字导致 `int()` 抛 500；
  - **修复**：实现 `_validate_positive_int_id`，利用正整数正则 `^[1-9]\d*$` 与显式 `bool` 拦截实现 fail-closed；
- **F-4 (低危，负向用例缺失)**：增设覆盖 7 种边界非法形态的自动化测试；
- **主档登记**：将 F-OPT81-01 ～ F-OPT81-04 录入 `sys_finding`（#9777～#9780）并回读核准。

### 2.3 第三轮：生产部署与跨项目不变量钉牢 (Commit: `db4fb98`)
- 升版至 `0.8.1`（`pyproject.toml` & `src/logbook/__init__.py`）；
- 增补 `test_record_query_cross_project_id_isolation` 用例，钉死 `WHERE project_id = $1 AND id = $2` 过滤不变量；
- 生产环境 34MB 全量冷备（`logbook_pre_0.8.1_20261115_1137.dump`）；
- 校正历史迁移 01 注释脱敏引起的校验和偏差，落实 §4.5 显式双容器协同重启（`docker compose down && docker compose up -d`），避免网络命名空间断裂。

### 2.4 第四轮：真实活体探针穿透与方案 A 极简封版 (Commit: `b3fdc76`)
用户在隔离靶项目 `probe` 跑活体测试，发现 Pydantic 框架层的静态转换漏洞：
- **F-12 (中危)**：`record_upsert(id=true)` 在进入函数前被 Pydantic v2 默认宽松模式强转为 `id=1`，内部的 `bool` 拦截被框架绕过；
  - **修复 (方案 A)**：引入 `pydantic.StrictInt`，在 `record_upsert` 与 `record_query` 入口层将 `id` 与 `parent_id` 声明为 `StrictInt`，框架层直接拒绝布尔值（`Input should be a valid integer [input_value=True, input_type=bool]`）；
  - **真机活体验证**：现场通过 SSH MCP 验证 `record_upsert(id=true)` 报 422/INVALID_ARGUMENT 阻断，误篡改 1 号记录隐患彻底消除；
  - **主档登记**：录入 `F-OPT81-12`（#9785）并回读校验。
- **其余 5 项按 SSOT 规程封存备案**：
  - **F-11**：立下铁律口径——**有父级单据一律按 ID 更新**；
  - **F-14**：单据唯一归属数据集，正规流程严禁跨数据集变更；
  - **F-15a**：全量更新（`patch=False`）遵循替换语义，局部更新务必显式声明 `patch=True`；
  - **F-15b**：`patch=True` 默认保全已有父级；
  - **F-13**：反序列化异常与业务数值校验分层防御。

---

## 3. 核心代码变更清单

| 文件 | 变更性质 | 核心逻辑与职责 |
|---|---|---|
| [src/logbook/mcp_server.py](file:///home/yupeng/logbook/src/logbook/mcp_server.py) | 核心原语 | ① `record_query` 增设 `StrictInt` 标量/数组过滤与 `applied_filters["id"]` 回显；<br>② `record_upsert` 增设 `patch: bool = False`，入参 `id` / `parent_id` 采用 `StrictInt` 严格类型；<br>③ `_validate_positive_int_id` 严格校验正整数。 |
| [src/logbook/models.py](file:///home/yupeng/logbook/src/logbook/models.py) | 数据模型 | ① `DataRecord` 的 `id` 与 `parent_id` 升级为 `StrictInt`；<br>② `doc_no` 声明为可选；<br>③ `patch: bool = False` 标记注入。 |
| [src/logbook/db.py](file:///home/yupeng/logbook/src/logbook/db.py) | 存储底座 | ① `upsert_record` 穿透 `model_fields_set`，在 `patch=True` 时保全所有未传强脊柱列；<br>② 合并 payload 后再执行 Schema 校验；<br>③ `query_records` 增补参数化 ID 过滤。 |
| [src/logbook/engine.py](file:///home/yupeng/logbook/src/logbook/engine.py) | 校验引擎 | ① `validate_payload_schema` 抛错输出必填字段清单与自愈资源引导。 |
| [tests/test_opt_081_symmetry_and_patch.py](file:///home/yupeng/logbook/tests/test_opt_081_symmetry_and_patch.py) | 专项门禁 | ① 7 大专项用例（80 门禁全绿），深入断言真实 SQL 绑定参数，覆盖 8 种负向边界。 |
| [docs/DEVLOG.md](file:///home/yupeng/logbook/docs/DEVLOG.md) | 台账正典 | ① 发现台账扩充至 41 项；<br>② `DEV-2026-11-14-01` 详细四要素复盘归档并办结。 |

---

## 4. 生产环境部署基线 (QNAP TS-453D)

- **宿主设备**：QNAP TS-453D (`Host nas`, `192.168.1.33`)；
- **服务网络**：`192.168.1.68` (`macvlan qnet-static-eth1-79e6cc`)；
- **运行容器栈**：
  - `logbook-app`: 镜像 `logbook:0.8.1`（SHA: `905578e6e0b7`，内存上限 256MB，运行正常）；
  - `logbook-postgres`: 镜像 `pgvector/pgvector:pg18`（内存上限 128MB，运行正常）；
- **生产冷备指针**：
  - `/share/CACHEDEV1_DATA/Container/logbook/backups/logbook_pre_0.8.1_20261115_1137.dump` (34MB)；
- **数据物理对账**：
  - 库内物理记录行数：**6,966 条**（零数据丢失，包含 5 项专项审计主档 #9777～#9780、#9785）。

---

## 5. 跨会话后续接续指引

- **系统状态**：已彻底收敛封版，进入免维护静止稳态；
- **远端同步**：本地 `main` 与 GitHub `origin/main` 保持绝对一致，工作区 100% 干净；
- **演进储备 (非阻断待办)**：
  - 若后续业务确有“解绑子单据父级”需求，在下一大版本规划显式 `clear_fields` 字段；
  - 若需重构定位键同构性，可择机评估将查询定位条件与 `uq_records_project_dataset_doc` 唯一约束对齐。
