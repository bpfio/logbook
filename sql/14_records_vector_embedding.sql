-- ============================================================================
-- Logbook 0.5.2: 通用事实表 (shared.records) 768维语义向量底座与批量操作支持
-- 1. shared.records: 增加 embedding vector(768) 字段与 HNSW 向量索引
-- 2. 授权 logbook_authenticated 角色读写 embedding 字段
-- 幂等执行。
-- ============================================================================

ALTER TABLE shared.records ADD COLUMN IF NOT EXISTS embedding vector(768);

CREATE INDEX IF NOT EXISTS idx_records_vector_hnsw ON shared.records USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
