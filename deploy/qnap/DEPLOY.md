# Logbook QNAP 部署 / 升级 Runbook

> 适用宿主机: QNAP Container Station（SSH 登录 NAS 执行）。
> 铁律: **任何升级先备份 PG**；一次只做一件事；改后核对容器状态与数据完整。
> 本 runbook 当前版本对应 `PLAN_OPT_2026-10-08` 三波升级（L-A1~A5 线）。

## 0. 前置检查

```bash
ssh <user>@<qnap>
cd <deploy-dir>                 # 含 compose.yaml 与 .env
docker compose ps               # 期望: logbook-app / pg 均 Up
docker exec logbook-pg psql -U postgres -d logbook -c "SELECT count(*) FROM shared.projects;"
```

记录升级前镜像 tag 与项目/数据量基线，供回滚对照。

## 1. PostgreSQL 备份（必须先行）

```bash
docker exec logbook-pg pg_dump -U postgres -d logbook -Fc \
  > /share/backups/logbook_pre_$(date +%Y%m%d_%H%M).dump
ls -lh /share/backups/          # 验证: dump 文件存在且体积与既往同量级（>0）
```

涉及存量数据清洗（如 devlog 去重 F14 ×4 副本）的波次，此步不可跳过。

## 2. 镜像重建

在开发机构建并推送到 NAS 可达的镜像位置（或 NAS 上 build）：

```bash
# 开发机
docker build -t logbook:<new-tag> .
docker save logbook:<new-tag> | ssh <user>@<qnap> docker load
```

验证点：`docker images | grep logbook` 出现新 tag；**旧 tag 保留不动**（回滚凭据）。

## 3. 更新 compose 并拉起

```bash
cd <deploy-dir>
cp compose.yaml.example compose.yaml   # 首次；已有则核对 .env 引用
# 确认 .env 含 LOGBOOK_DB_PASSWORD / EMBEDDING_API_KEY / SSH_PASSWORD（禁止明文入 yaml）
sed -i 's/image: logbook:.*/image: logbook:<new-tag>/' compose.yaml
docker compose up -d
docker compose ps               # 验证: 双容器 Up，无 Restarting
docker logs --tail 20 logbook-app   # 验证: 无 traceback，DB 连接正常
```

## 4. SQL 迁移（有迁移的波次）

迁移脚本执行顺序固定，**先决条件 = 第 1 步备份已完成**：

```bash
# 顺序: 01_init.sql（仅全新库）→ 02_optimize_fields.sql → 03_dedup.sql（波1 去重迁移，落地后新增）
docker exec -i logbook-pg psql -U postgres -d logbook < sql/02_optimize_fields.sql
docker exec -i logbook-pg psql -U postgres -d logbook < sql/03_dedup.sql
```

验证点（以 03_dedup 为例）：

```bash
# 去重后 (project, task_id, title) 重复计数应为 0
docker exec logbook-pg psql -U postgres -d logbook -c "
  SELECT project_id, task_id, title, count(*)
  FROM proj_brix.devlogs GROUP BY 1,2,3 HAVING count(*)>1;"
```

迁移脚本须幂等（重复执行不报错不重复清洗）；执行失败立即停止后续步骤，评估回滚。

## 5. brix MCP 回归

在 brix 工作区重放真实写入，验证幂等（**零新副本**为通过标准）：

```bash
cd /home/yupeng/brix
python3 ../logbook/scripts/replay_brix_writes.py    # 重放 2026-10-07/08 共 18 笔写入
```

验证点：

- 重放后再次执行第 4 步的重复计数 SQL = 0（写路径去重收口生效）；
- 抽测一次结构化错误（如对不存在 task_id 调 `finding_record`）：返回 `{isError, error_type: TASK_NOT_FOUND, detail}`，而非 "Error executing tool X"；
- `devlog_search` 抽查历史关键词，结果无同笔重复。

## 6. 回滚

任一验证点失败即回滚，不要带病迭代：

```bash
# 6.1 镜像回退
cd <deploy-dir>
sed -i 's/image: logbook:.*/image: logbook:<previous-tag>/' compose.yaml
docker compose up -d

# 6.2 数据回退（仅当执行过迁移/清洗后需要）
docker compose stop logbook-app
docker exec -i logbook-pg dropdb -U postgres --force logbook
docker exec -i logbook-pg createdb -U postgres logbook
cat /share/backups/logbook_pre_<timestamp>.dump | docker exec -i logbook-pg pg_restore -U postgres -d logbook
docker compose start logbook-app
```

回滚后重跑第 0 步前置检查 + 第 5 步回归确认恢复原状。
