-- ============================================================================
-- Logbook 0.5.0: 通用事实引擎与数据集管理 (13_universal_datasets_and_records.sql)
-- 1. shared.datasets: 统一数据集元数据字典表 (Schema & Types)
-- 2. shared.records: 通用事实数据表 (强脊梁 + 软肋骨 + 主子表支持)
-- 3. 专用 GIN (jsonb_path_ops) 索引与全文检索倒排索引
-- 4. RLS 行级安全绑定现有多用户与分组机制
-- 幂等执行。
-- ============================================================================

-- ----------------------------------------------------------------------------
-- 一、 数据集元数据字典表: shared.datasets
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shared.datasets (
    code            VARCHAR(64) PRIMARY KEY,              -- 唯一编码 (如 'safe_forex', 'oss_benchmark')
    name            VARCHAR(256) NOT NULL,                 -- 中文名称 (如 '人行涉外收汇申报')
    category        VARCHAR(32) NOT NULL DEFAULT 'general', -- 分类: finance, oss_metrics, trade, tax, research, general
    field_schema    JSONB NOT NULL DEFAULT '[]'::jsonb,   -- 字段元数据定义列表 [{"key": "...", "label": "...", "type": "..."}]
    alias_map       JSONB NOT NULL DEFAULT '{}'::jsonb,   -- 历史版本与同义词映射
    description     TEXT,                                 -- 数据集业务口径与填报说明
    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_datasets_category ON shared.datasets(category);

-- ----------------------------------------------------------------------------
-- 二、 通用事实数据表: shared.records
-- ----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS shared.records (
    id              BIGSERIAL PRIMARY KEY,
    project_id      VARCHAR(64) NOT NULL,                 -- 归属项目代号 (如 'brix', 'logbook')
    dataset_code    VARCHAR(64) NOT NULL REFERENCES shared.datasets(code) ON DELETE RESTRICT,
    parent_id       BIGINT REFERENCES shared.records(id) ON DELETE CASCADE, -- 主子表自引用外键
    batch_id        VARCHAR(64),                          -- 导入批次号 (支持按 Excel 批次追溯、统计与回滚)

    -- 强脊梁 (Strong Spine)
    doc_no          VARCHAR(128) NOT NULL,                -- 核心单据号/唯一标识
    entity_name     VARCHAR(256),                         -- 交易对手/往来单位/组件仓库名
    amount          NUMERIC(18, 4) NOT NULL DEFAULT 0,    -- 核心金额/指标数值 (高精度, 无浮点误差)
    currency        VARCHAR(16) NOT NULL DEFAULT 'CNY',   -- 币种/量纲单位 (USD, CNY, QPS, MS 等)
    event_date      DATE NOT NULL,                        -- 业务发生日期/测试日期
    status          VARCHAR(32) NOT NULL DEFAULT 'recorded', -- 状态: recorded, reconciled, verified, cancelled

    -- 软肋骨 (Flexible Ribs)
    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,   -- 动态属性载荷 (容纳 10~100 个业务特有字段)

    -- 全文检索生成列
    tsv_content     tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', coalesce(doc_no, '') || ' ' || coalesce(entity_name, '') || ' ' || payload::text)
    ) STORED,

    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 高性能专用索引阵列
CREATE INDEX IF NOT EXISTS idx_records_scope ON shared.records (project_id, dataset_code, event_date);
CREATE INDEX IF NOT EXISTS idx_records_doc ON shared.records (project_id, doc_no);
CREATE INDEX IF NOT EXISTS idx_records_parent ON shared.records (parent_id) WHERE parent_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_records_payload ON shared.records USING GIN (payload jsonb_path_ops);
CREATE INDEX IF NOT EXISTS idx_records_tsv ON shared.records USING GIN (tsv_content);

-- ----------------------------------------------------------------------------
-- 三、 行级安全 (RLS) 与权限集成
-- ----------------------------------------------------------------------------
ALTER TABLE shared.records ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_proc nsp_p 
        JOIN pg_namespace nsp ON nsp.oid = nsp_p.pronamespace 
        WHERE nsp.nspname = 'shared' AND nsp_p.proname = 'acl_project_visible'
    ) THEN
        DROP POLICY IF EXISTS p_records_select ON shared.records;
        DROP POLICY IF EXISTS p_records_insert ON shared.records;
        DROP POLICY IF EXISTS p_records_update ON shared.records;
        DROP POLICY IF EXISTS p_records_delete ON shared.records;

        CREATE POLICY p_records_select ON shared.records
            FOR SELECT
            USING (shared.acl_project_visible(project_id));
        CREATE POLICY p_records_insert ON shared.records
            FOR INSERT
            WITH CHECK (shared.acl_project_visible(project_id));
        CREATE POLICY p_records_update ON shared.records
            FOR UPDATE
            USING (shared.acl_project_visible(project_id))
            WITH CHECK (shared.acl_project_visible(project_id));
        CREATE POLICY p_records_delete ON shared.records
            FOR DELETE
            USING (shared.acl_project_visible(project_id));
    END IF;

    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'logbook_authenticated') THEN
        GRANT SELECT, INSERT, UPDATE ON shared.datasets TO logbook_authenticated;
        GRANT SELECT, INSERT, UPDATE, DELETE ON shared.records TO logbook_authenticated;
        GRANT USAGE, SELECT ON SEQUENCE shared.records_id_seq TO logbook_authenticated;
    END IF;
END $$;
