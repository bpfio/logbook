-- ============================================================================
-- 03_dedup.sql — P0-1 写路径统一 + 去重收口迁移
-- 目标:
--   1) 清洗 shared.devlogs 历史重复副本 (同 (project_id, task_id, title) 组保留 max(id))
--      task_id 为 NULL 的副本按 '' 归组 (与唯一表达式索引口径一致)
--   2) 建唯一表达式索引，收口幂等键塌缩 (病2)
-- 幂等: 可重复执行；重复行未清空前不建索引 (建索引前先删重)
-- ============================================================================

-- 1. 删除重复副本: 保留每组 max(id) (最新副本，其 HNSW 向量随行保留)
--    embedding 存于 devlogs 同表，行删除时向量与索引条目自动随之自愈
DELETE FROM shared.devlogs a
USING shared.devlogs b
WHERE a.id < b.id
  AND a.project_id = b.project_id
  AND COALESCE(a.task_id, '') = COALESCE(b.task_id, '')
  AND a.title = b.title;

-- 2. 唯一表达式索引 (task_id 允许 NULL，用 COALESCE 归一为 '' 参与唯一性)
CREATE UNIQUE INDEX IF NOT EXISTS uq_devlogs_proj_task_title
    ON shared.devlogs (project_id, COALESCE(task_id, ''), title);
