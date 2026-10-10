-- ============================================================================
-- Logbook 0.4.0: 研发调研知识库 (Research Knowledge Base) DDL
-- 幂等迁移: 共享知识面 shared.researches (调研五要素 + 512维向量 + GIN全文检索 + 审计三要素)
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS shared.researches (
    id BIGSERIAL PRIMARY KEY,
    project_id VARCHAR(32) NOT NULL,             -- 归属项目代号 (如 'brix', 'logbook')
    task_id VARCHAR(32),                         -- 关联研发任务短编号 (如 'L14', 'T101')
    batch_id VARCHAR(32),                        -- 关联研发批次号 (如 'DEV-2026-10-09-01')
    title VARCHAR(256) NOT NULL,                 -- 调研主题 (如 'Python 高性能异步 SSH 客户端选型')
    category VARCHAR(32) NOT NULL DEFAULT 'architecture' 
        CHECK (category IN ('architecture', 'database', 'network', 'kernel', 'security', 'library', 'tooling')),
    author VARCHAR(64) NOT NULL DEFAULT 'agy',   -- 调研者 Agent 或人类专家 (Who)
    agent_ip VARCHAR(45) NOT NULL DEFAULT '0.0.0.0', -- 节点 IP 溯源审计 (Where, v0.3.1 规格)
    status VARCHAR(16) NOT NULL DEFAULT 'completed'
        CHECK (status IN ('in_progress', 'completed', 'deprecated')),

    -- =========================================================================
    -- 调研结构化五要素 (必填，贯彻铁律三：禁止重复造轮子与评估对抗成本)
    -- =========================================================================
    objective TEXT NOT NULL,                     -- 1.【调研目标/背景】业务诉求、问题边界与资源底线
    market_landscape TEXT NOT NULL,              -- 2.【成熟方案全景】社区已有方案清单 (Star数/维护度/生产验证)
    tradeoffs TEXT NOT NULL,                     -- 3.【两路线评估与对抗成本】引入 vs 自研代价；对抗默认行为清单
    decision TEXT NOT NULL,                      -- 4.【最终选型决策】选了什么、为什么不用自己写、设计哲学冲突规避策略
    "references" TEXT,                           -- 5.【事实依据与文献】官方文档、Benchmark 数据源、GitHub Repo 锚点

    -- 密级防线与检索加速
    visibility VARCHAR(16) NOT NULL DEFAULT 'project_private'
        CHECK (visibility IN ('project_private', 'org_internal', 'public_safe')),
    tags VARCHAR(32)[] DEFAULT '{}',             -- 标签 (如 ['asyncssh', 'ssh', 'audit'])
    embedding vector(512),                       -- 512 维向量 (讯飞星火 MaaS / 本地降级向量)
    tsv_content tsvector GENERATED ALWAYS AS (   -- 全文检索虚拟列
        to_tsvector('simple', title || ' ' || objective || ' ' || market_landscape || ' ' || decision)
    ) STORED,

    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP, -- (When)
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 幂等防御加列
ALTER TABLE shared.researches ADD COLUMN IF NOT EXISTS agent_ip VARCHAR(45) NOT NULL DEFAULT '0.0.0.0';
ALTER TABLE shared.researches ADD COLUMN IF NOT EXISTS batch_id VARCHAR(32);
ALTER TABLE shared.researches ADD COLUMN IF NOT EXISTS "references" TEXT;

-- 检索与审计索引规划
CREATE INDEX IF NOT EXISTS idx_researches_project_vis ON shared.researches(project_id, visibility);
CREATE INDEX IF NOT EXISTS idx_researches_agent_ip ON shared.researches(project_id, agent_ip);
CREATE INDEX IF NOT EXISTS idx_researches_category ON shared.researches(category);
CREATE INDEX IF NOT EXISTS idx_researches_status ON shared.researches(status);
CREATE INDEX IF NOT EXISTS idx_researches_tsv ON shared.researches USING gin(tsv_content);
CREATE INDEX IF NOT EXISTS idx_researches_vector_hnsw ON shared.researches USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);
