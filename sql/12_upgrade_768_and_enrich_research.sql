-- ============================================================================
-- Logbook 0.5.0: 向量底座升级为 768 维与调研知识库多领域扩容 (12_upgrade_768_and_enrich_research.sql)
-- 1. 向量维度由 512 升级为 768 维:
--    - shared.devlogs: embedding vector(768) + 重建 HNSW 索引
--    - shared.researches: embedding vector(768) + 重建 HNSW 索引
--    - 存量向量平滑置空待一键重算回填, 文本与元数据 100% 完整保留
-- 2. 调研知识库 shared.researches 丰富度扩容 (方案 B):
--    - 新增 doc_no (发文字号/标准编号/权威编码)
--    - 新增 authority (发文机构/起草部委/组织/学派)
--    - 新增 legal_level (效力位阶/标准层级/权威度)
--    - 新增 effective_date (生效实施日期)
--    - 放宽/移除 category 与 status 的硬编码限制, 开放政策智库与多领域研究
--    - 升级 tsv_content 全文倒排索引, 融合 doc_no 与 authority 关键词
-- 幂等执行。
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 一、 shared.devlogs 升级为 768 维向量
-- ----------------------------------------------------------------------------
DROP INDEX IF EXISTS shared.idx_devlogs_vector_hnsw;
UPDATE shared.devlogs SET embedding = NULL;
ALTER TABLE shared.devlogs ALTER COLUMN embedding TYPE vector(768);
CREATE INDEX IF NOT EXISTS idx_devlogs_vector_hnsw ON shared.devlogs USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ----------------------------------------------------------------------------
-- 二、 shared.researches 升级为 768 维向量与字段扩容
-- ----------------------------------------------------------------------------
DROP INDEX IF EXISTS shared.idx_researches_vector_hnsw;
UPDATE shared.researches SET embedding = NULL;
ALTER TABLE shared.researches ALTER COLUMN embedding TYPE vector(768);
CREATE INDEX IF NOT EXISTS idx_researches_vector_hnsw ON shared.researches USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- 扩容元数据列
ALTER TABLE shared.researches ADD COLUMN IF NOT EXISTS doc_no VARCHAR(128);
ALTER TABLE shared.researches ADD COLUMN IF NOT EXISTS authority VARCHAR(128);
ALTER TABLE shared.researches ADD COLUMN IF NOT EXISTS legal_level VARCHAR(32);
ALTER TABLE shared.researches ADD COLUMN IF NOT EXISTS effective_date VARCHAR(32);

-- 索引规划
CREATE INDEX IF NOT EXISTS idx_researches_doc_no ON shared.researches(doc_no);
CREATE INDEX IF NOT EXISTS idx_researches_authority ON shared.researches(authority);

-- 放宽 category 与 status 的旧约束
DO $$
DECLARE
    c RECORD;
BEGIN
    FOR c IN 
        SELECT con.conname 
        FROM pg_constraint con
        JOIN pg_namespace nsp ON nsp.oid = con.connamespace
        JOIN pg_class cls ON cls.oid = con.conrelid
        JOIN pg_attribute att ON att.attrelid = cls.oid AND att.attnum = ANY(con.conkey)
        WHERE nsp.nspname = 'shared'
          AND cls.relname = 'researches'
          AND con.contype = 'c'
          AND att.attname IN ('category', 'status')
    LOOP
        EXECUTE format('ALTER TABLE shared.researches DROP CONSTRAINT IF EXISTS %I;', c.conname);
    END LOOP;
END $$;

-- 升级全文检索 GENERATED ALWAYS 列以包含 doc_no 与 authority
ALTER TABLE shared.researches DROP COLUMN IF EXISTS tsv_content CASCADE;
ALTER TABLE shared.researches ADD COLUMN tsv_content tsvector GENERATED ALWAYS AS (
    to_tsvector('simple', COALESCE(title, '') || ' ' || COALESCE(doc_no, '') || ' ' || COALESCE(authority, '') || ' ' || COALESCE(objective, '') || ' ' || COALESCE(market_landscape, '') || ' ' || COALESCE(decision, ''))
) STORED;
CREATE INDEX IF NOT EXISTS idx_researches_tsv ON shared.researches USING gin(tsv_content);
