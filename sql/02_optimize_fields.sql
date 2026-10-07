-- ============================================================================
-- Logbook 字段优化与审计扩容平滑迁移脚本 (02_optimize_fields.sql)
-- 幂等执行: 采用 ADD COLUMN IF NOT EXISTS 与动态 Schema 遍历
-- ============================================================================

-- 1. 架构铁律扩容
ALTER TABLE shared.rules ADD COLUMN IF NOT EXISTS category VARCHAR(32) NOT NULL DEFAULT 'general';
CREATE INDEX IF NOT EXISTS idx_rules_category ON shared.rules(category);

-- 2. 排查手记扩容
ALTER TABLE shared.devlogs ADD COLUMN IF NOT EXISTS author VARCHAR(64) NOT NULL DEFAULT 'agy';
ALTER TABLE shared.devlogs ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP;

-- 3. 动态项目执行面模板函数更新 (保证未来创建的新项目 Schema 包含新字段)
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
            parent_id VARCHAR(32) REFERENCES %1$I.tasks(id) ON DELETE SET NULL,
            title VARCHAR(256) NOT NULL,
            task_type VARCHAR(16) NOT NULL CHECK (task_type IN (''feat'', ''fix'', ''verify'', ''investigation'', ''drill'', ''docs'', ''ops'', ''deploy'')),
            priority VARCHAR(4) NOT NULL CHECK (priority IN (''P0'', ''P1'', ''P2'', ''P3'')),
            status VARCHAR(16) NOT NULL CHECK (status IN (''planned'', ''running'', ''blocked'', ''closed'', ''wontfix'')),
            assignee VARCHAR(64) DEFAULT ''agy'',
            commit_hash VARCHAR(128),
            proof_link VARCHAR(512),
            notes TEXT,
            tags VARCHAR(32)[] DEFAULT ''{}'',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
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
            reporter VARCHAR(64) DEFAULT ''audit'',
            summary TEXT NOT NULL,
            resolution TEXT,
            discovered_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMPTZ
        );

        -- 4. 待办与待用户裁决表 (Waitings)
        CREATE TABLE IF NOT EXISTS %1$I.waitings (
            id VARCHAR(32) PRIMARY KEY,
            category VARCHAR(16) NOT NULL CHECK (category IN (''user'', ''closing'', ''external'')),
            owner VARCHAR(64) DEFAULT ''user'',
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
        CREATE INDEX IF NOT EXISTS idx_%1$s_tasks_parent ON %1$I.tasks(parent_id);
        CREATE INDEX IF NOT EXISTS idx_%1$s_tasks_assignee ON %1$I.tasks(assignee);
        CREATE INDEX IF NOT EXISTS idx_%1$s_findings_status ON %1$I.findings(status);
        CREATE INDEX IF NOT EXISTS idx_%1$s_waitings_status ON %1$I.waitings(status);
        CREATE INDEX IF NOT EXISTS idx_%1$s_timeline_task ON %1$I.task_timeline(task_id, occurred_at);
    ', p_name);

    EXECUTE schema_sql;
END;
$$ LANGUAGE plpgsql;

-- 4. 对所有存量业务项目 Schema 幂等扩容
DO $$
DECLARE
    r RECORD;
BEGIN
    FOR r IN SELECT schema_name FROM information_schema.schemata 
             WHERE schema_name NOT IN ('information_schema', 'pg_catalog', 'pg_toast', 'shared', 'public')
               AND schema_name NOT LIKE 'pg_%'
    LOOP
        -- 仅当该 schema 存在 tasks 表时执行变更
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = r.schema_name AND table_name = 'tasks') THEN
            EXECUTE format('ALTER TABLE %I.tasks ADD COLUMN IF NOT EXISTS assignee VARCHAR(64) DEFAULT ''agy'';', r.schema_name);
            EXECUTE format('ALTER TABLE %I.tasks ADD COLUMN IF NOT EXISTS parent_id VARCHAR(32);', r.schema_name);
            EXECUTE format('ALTER TABLE %I.tasks ADD COLUMN IF NOT EXISTS tags VARCHAR(32)[] DEFAULT ''{}'';', r.schema_name);
            EXECUTE format('ALTER TABLE %I.tasks ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP;', r.schema_name);
            EXECUTE format('CREATE INDEX IF NOT EXISTS idx_%s_tasks_parent ON %I.tasks(parent_id);', r.schema_name, r.schema_name);
            EXECUTE format('CREATE INDEX IF NOT EXISTS idx_%s_tasks_assignee ON %I.tasks(assignee);', r.schema_name, r.schema_name);
        END IF;

        -- findings
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = r.schema_name AND table_name = 'findings') THEN
            EXECUTE format('ALTER TABLE %I.findings ADD COLUMN IF NOT EXISTS reporter VARCHAR(64) DEFAULT ''audit'';', r.schema_name);
        END IF;

        -- waitings
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = r.schema_name AND table_name = 'waitings') THEN
            EXECUTE format('ALTER TABLE %I.waitings ADD COLUMN IF NOT EXISTS owner VARCHAR(64) DEFAULT ''user'';', r.schema_name);
        END IF;

        -- task_timeline
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = r.schema_name AND table_name = 'task_timeline') THEN
            EXECUTE format('ALTER TABLE %I.task_timeline ADD COLUMN IF NOT EXISTS from_status VARCHAR(16);', r.schema_name);
        END IF;
    END LOOP;
END $$;
