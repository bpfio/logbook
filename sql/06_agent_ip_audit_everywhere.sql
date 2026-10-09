-- ============================================================================
-- Logbook 0.3.1: 全实体 Agent IP 溯源与不可篡改审计增强迁移脚本 (06_agent_ip_audit_everywhere.sql)
-- 幂等执行: 遍历共享面与所有动态项目 Schema
-- ============================================================================

-- 1. 全局共享面：排查手记与文件租约锁增量加列
ALTER TABLE shared.devlogs ADD COLUMN IF NOT EXISTS agent_ip VARCHAR(45) NOT NULL DEFAULT '0.0.0.0';
ALTER TABLE shared.file_leases ADD COLUMN IF NOT EXISTS agent_ip VARCHAR(45) NOT NULL DEFAULT '0.0.0.0';

CREATE INDEX IF NOT EXISTS idx_devlogs_agent_ip ON shared.devlogs(project_id, agent_ip);
CREATE INDEX IF NOT EXISTS idx_file_leases_agent_ip ON shared.file_leases(project_id, agent_ip);

-- 2. 动态项目执行面：遍历所有非系统 Schema，为 tasks / findings / waitings / batches / task_timeline 追加 agent_ip
DO $$
DECLARE
    r RECORD;
    s_name TEXT;
BEGIN
    FOR r IN SELECT schema_name FROM information_schema.schemata 
             WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'public', 'shared')
    LOOP
        s_name := r.schema_name;
        
        -- 2.1 tasks
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = s_name AND table_name = 'tasks') THEN
            EXECUTE format('ALTER TABLE %I.tasks ADD COLUMN IF NOT EXISTS agent_ip VARCHAR(45) NOT NULL DEFAULT ''0.0.0.0'';', s_name);
            EXECUTE format('CREATE INDEX IF NOT EXISTS idx_%s_tasks_agent_ip ON %I.tasks(agent_ip);', s_name, s_name);
        END IF;

        -- 2.2 findings
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = s_name AND table_name = 'findings') THEN
            EXECUTE format('ALTER TABLE %I.findings ADD COLUMN IF NOT EXISTS agent_ip VARCHAR(45) NOT NULL DEFAULT ''0.0.0.0'';', s_name);
            EXECUTE format('CREATE INDEX IF NOT EXISTS idx_%s_findings_agent_ip ON %I.findings(agent_ip);', s_name, s_name);
        END IF;

        -- 2.3 waitings
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = s_name AND table_name = 'waitings') THEN
            EXECUTE format('ALTER TABLE %I.waitings ADD COLUMN IF NOT EXISTS agent_ip VARCHAR(45) NOT NULL DEFAULT ''0.0.0.0'';', s_name);
            EXECUTE format('CREATE INDEX IF NOT EXISTS idx_%s_waitings_agent_ip ON %I.waitings(agent_ip);', s_name, s_name);
        END IF;

        -- 2.4 batches
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = s_name AND table_name = 'batches') THEN
            EXECUTE format('ALTER TABLE %I.batches ADD COLUMN IF NOT EXISTS agent_ip VARCHAR(45) NOT NULL DEFAULT ''0.0.0.0'';', s_name);
            EXECUTE format('CREATE INDEX IF NOT EXISTS idx_%s_batches_agent_ip ON %I.batches(agent_ip);', s_name, s_name);
        END IF;

        -- 2.5 task_timeline
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = s_name AND table_name = 'task_timeline') THEN
            EXECUTE format('ALTER TABLE %I.task_timeline ADD COLUMN IF NOT EXISTS agent_ip VARCHAR(45) NOT NULL DEFAULT ''0.0.0.0'';', s_name);
        END IF;
    END LOOP;
END;
$$;

-- 3. 更新共享函数 shared.init_project_schema，确保未来新开立的项目默认包含 agent_ip
CREATE OR REPLACE FUNCTION shared.init_project_schema(p_name TEXT) 
RETURNS VOID AS $$
DECLARE
    schema_sql TEXT;
BEGIN
    schema_sql := format('
        CREATE SCHEMA IF NOT EXISTS %1$I;

        CREATE TABLE IF NOT EXISTS %1$I.batches (
            id VARCHAR(32) PRIMARY KEY,
            title VARCHAR(256) NOT NULL,
            status VARCHAR(16) NOT NULL CHECK (status IN (''planned'', ''running'', ''completed'', ''halted'')),
            branch_name VARCHAR(128),
            summary TEXT,
            methodology_notes TEXT,
            agent_ip VARCHAR(45) NOT NULL DEFAULT ''0.0.0.0'',
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMPTZ
        );

        CREATE TABLE IF NOT EXISTS %1$I.tasks (
            id VARCHAR(32) PRIMARY KEY,
            batch_id VARCHAR(32) REFERENCES %1$I.batches(id) ON DELETE SET NULL,
            parent_id VARCHAR(32) REFERENCES %1$I.tasks(id) ON DELETE SET NULL,
            title VARCHAR(256) NOT NULL,
            task_type VARCHAR(16) NOT NULL CHECK (task_type IN (''feat'', ''fix'', ''verify'', ''investigation'', ''drill'', ''docs'', ''ops'', ''deploy'')),
            priority VARCHAR(4) NOT NULL CHECK (priority IN (''P0'', ''P1'', ''P2'', ''P3'')),
            status VARCHAR(16) NOT NULL CHECK (status IN (''planned'', ''running'', ''blocked'', ''closed'', ''wontfix'', ''review'')),
            assignee VARCHAR(64) DEFAULT ''agy'',
            reviewer VARCHAR(64) DEFAULT ''zcode'',
            agent_ip VARCHAR(45) NOT NULL DEFAULT ''0.0.0.0'',
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

        CREATE TABLE IF NOT EXISTS %1$I.findings (
            id VARCHAR(64) PRIMARY KEY,
            source VARCHAR(64) NOT NULL,
            severity VARCHAR(4) NOT NULL CHECK (severity IN (''P1'', ''P2'', ''P3'')),
            status VARCHAR(16) NOT NULL CHECK (status IN (''open'', ''infix'', ''fixed'', ''wontfix'', ''blocked'')),
            task_id VARCHAR(32) REFERENCES %1$I.tasks(id) ON DELETE SET NULL,
            reporter VARCHAR(64) DEFAULT ''audit'',
            agent_ip VARCHAR(45) NOT NULL DEFAULT ''0.0.0.0'',
            summary TEXT NOT NULL,
            resolution TEXT,
            discovered_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMPTZ
        );

        CREATE TABLE IF NOT EXISTS %1$I.waitings (
            id VARCHAR(32) PRIMARY KEY,
            category VARCHAR(16) NOT NULL CHECK (category IN (''user'', ''closing'', ''external'')),
            owner VARCHAR(64) DEFAULT ''user'',
            status VARCHAR(16) NOT NULL CHECK (status IN (''open'', ''closed'')),
            agent_ip VARCHAR(45) NOT NULL DEFAULT ''0.0.0.0'',
            description TEXT NOT NULL,
            resolution TEXT,
            blocked_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            resolved_at TIMESTAMPTZ
        );

        CREATE TABLE IF NOT EXISTS %1$I.task_timeline (
            id BIGSERIAL PRIMARY KEY,
            task_id VARCHAR(32) NOT NULL REFERENCES %1$I.tasks(id) ON DELETE CASCADE,
            from_status VARCHAR(16),
            to_status VARCHAR(16) NOT NULL,
            operator VARCHAR(64) NOT NULL,
            agent_ip VARCHAR(45) NOT NULL DEFAULT ''0.0.0.0'',
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
