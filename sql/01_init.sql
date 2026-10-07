-- ============================================================================
-- Logbook PostgreSQL 18 + pgvector 初始化 DDL (双平面架构)
-- SSOT: /home/yupeng/logbook/AGENTS.md
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ============================================================================
-- 一、 共享知识面 (Shared Knowledge Plane: Schema shared)
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS shared;

-- 1. 架构铁律与工程红线表 (Rules)
CREATE TABLE IF NOT EXISTS shared.rules (
    id VARCHAR(64) PRIMARY KEY,                  -- 规则标识，如 'RULE-NET-01'
    title VARCHAR(256) NOT NULL,
    summary TEXT NOT NULL,                       -- 一句话核心原则
    bad_practice TEXT NOT NULL,                  -- 错误示范 (Anti-Pattern)
    good_practice TEXT NOT NULL,                 -- 正确做法 (Best Practice)
    constraints TEXT,                            -- 边界约束与红线说明
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 2. 排查手记与长期记忆表 (DevLogs: 故障四要素 + 512维向量 + 全文检索)
CREATE TABLE IF NOT EXISTS shared.devlogs (
    id BIGSERIAL PRIMARY KEY,
    project_id VARCHAR(32) NOT NULL,             -- 归属项目 (如 'brix', 'logbook')
    task_id VARCHAR(32),                         -- 关联项目任务 ID (如 'F14')
    title VARCHAR(256) NOT NULL,
    -- 结构化根因四要素 (必填)
    problem TEXT NOT NULL,                       -- 故障现象与复现路径
    root_cause TEXT NOT NULL,                    -- 机理定位与代码/内核穿透分析
    solution TEXT NOT NULL,                      -- 明确修复逻辑与架构重构
    evidence TEXT NOT NULL,                      -- 修复前后实测对比证据
    -- 密级防线
    visibility VARCHAR(16) NOT NULL DEFAULT 'project_private'
        CHECK (visibility IN ('project_private', 'org_internal', 'public_safe')),
    -- 检索加速字段
    tags VARCHAR(32)[] DEFAULT '{}',
    embedding vector(512),                       -- 512 维向量 (讯飞星火 MaaS)
    tsv_content tsvector GENERATED ALWAYS AS (
        to_tsvector('simple', title || ' ' || problem || ' ' || root_cause || ' ' || solution)
    ) STORED,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_devlogs_project_vis ON shared.devlogs(project_id, visibility);
CREATE INDEX IF NOT EXISTS idx_devlogs_tsv ON shared.devlogs USING gin(tsv_content);
CREATE INDEX IF NOT EXISTS idx_devlogs_vector_hnsw ON shared.devlogs USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ============================================================================
-- 二、 动态项目执行面 DDL 模板函数 (Project Execution Plane)
-- 任何新项目注册均调用此函数完成 Schema 及表结构幂等初始化
-- ============================================================================
CREATE OR REPLACE FUNCTION shared.init_project_schema(p_name TEXT) 
RETURNS VOID AS $$
DECLARE
    schema_sql TEXT;
BEGIN
    schema_sql := format('
        CREATE SCHEMA IF NOT EXISTS %1$I;

        -- 1. 研发批次表 (Batches)
        CREATE TABLE IF NOT EXISTS %1$I.batches (
            id VARCHAR(32) PRIMARY KEY,
            title VARCHAR(256) NOT NULL,
            status VARCHAR(16) NOT NULL CHECK (status IN (''planned'', ''running'', ''completed'', ''halted'')),
            branch_name VARCHAR(128),
            summary TEXT,
            methodology_notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMPTZ
        );

        -- 2. 任务台账表 (Tasks)
        CREATE TABLE IF NOT EXISTS %1$I.tasks (
            id VARCHAR(32) PRIMARY KEY,
            batch_id VARCHAR(32) REFERENCES %1$I.batches(id) ON DELETE SET NULL,
            title VARCHAR(256) NOT NULL,
            task_type VARCHAR(16) NOT NULL CHECK (task_type IN (''feat'', ''fix'', ''verify'', ''investigation'', ''drill'', ''docs'', ''ops'', ''deploy'')),
            priority VARCHAR(4) NOT NULL CHECK (priority IN (''P0'', ''P1'', ''P2'', ''P3'')),
            status VARCHAR(16) NOT NULL CHECK (status IN (''planned'', ''running'', ''blocked'', ''closed'', ''wontfix'')),
            commit_hash VARCHAR(128),
            proof_link VARCHAR(512),
            notes TEXT,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at TIMESTAMPTZ,
            closed_at TIMESTAMPTZ,
            duration_seconds INTEGER GENERATED ALWAYS AS (
                CASE WHEN closed_at IS NOT NULL AND started_at IS NOT NULL 
                     THEN EXTRACT(EPOCH FROM (closed_at - started_at))::INTEGER 
                     ELSE NULL END
            ) STORED,
            CONSTRAINT chk_task_closed_proof CHECK (
                status != ''closed'' OR (commit_hash IS NOT NULL OR proof_link IS NOT NULL)
            ),
            CONSTRAINT chk_task_closed_time CHECK (
                status != ''closed'' OR closed_at IS NOT NULL
            )
        );

        -- 3. 发现台账表 (Findings)
        CREATE TABLE IF NOT EXISTS %1$I.findings (
            id VARCHAR(64) PRIMARY KEY,
            source VARCHAR(64) NOT NULL,
            severity VARCHAR(4) NOT NULL CHECK (severity IN (''P1'', ''P2'', ''P3'')),
            status VARCHAR(16) NOT NULL CHECK (status IN (''open'', ''infix'', ''fixed'', ''wontfix'', ''blocked'')),
            task_id VARCHAR(32) REFERENCES %1$I.tasks(id) ON DELETE SET NULL,
            summary TEXT NOT NULL,
            resolution TEXT,
            discovered_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMPTZ
        );

        -- 4. 待办与待用户裁决表 (Waitings)
        CREATE TABLE IF NOT EXISTS %1$I.waitings (
            id VARCHAR(32) PRIMARY KEY,
            category VARCHAR(16) NOT NULL CHECK (category IN (''user'', ''closing'', ''external'')),
            status VARCHAR(16) NOT NULL CHECK (status IN (''open'', ''closed'')),
            description TEXT NOT NULL,
            resolution TEXT,
            blocked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMPTZ
        );

        -- 5. 任务时序审计流水表 (Task Timeline)
        CREATE TABLE IF NOT EXISTS %1$I.task_timeline (
            id BIGSERIAL PRIMARY KEY,
            task_id VARCHAR(32) NOT NULL REFERENCES %1$I.tasks(id) ON DELETE CASCADE,
            from_status VARCHAR(16),
            to_status VARCHAR(16) NOT NULL,
            operator VARCHAR(64) NOT NULL,
            occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            remark TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_%1$s_tasks_status ON %1$I.tasks(status, priority);
        CREATE INDEX IF NOT EXISTS idx_%1$s_findings_status ON %1$I.findings(status);
        CREATE INDEX IF NOT EXISTS idx_%1$s_waitings_status ON %1$I.waitings(status);
        CREATE INDEX IF NOT EXISTS idx_%1$s_timeline_task ON %1$I.task_timeline(task_id, occurred_at);
    ', p_name);

    EXECUTE schema_sql;
END;
$$ LANGUAGE plpgsql;

-- 初始化默认自带的项目 Schema
SELECT shared.init_project_schema('brix');
SELECT shared.init_project_schema('logbook');
