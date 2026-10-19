-- ============================================================================
-- Logbook 0.5.0: 任务类型扩容支持叙事与创作工程 (11_creative_task_types.sql)
-- - 扩充 task_type: outline, draft, polish, continuity, lore
-- - 闭环断言支持: 允许详实 notes 作为创作与非代码任务的 closed 证据
-- - 更新 shared.init_project_schema 模板函数
-- - 动态平滑更新存量所有项目 Schema 下 tasks 表的 CHECK 约束
-- 幂等执行。
-- ============================================================================

-- 1. 更新动态项目执行面模板函数 (新项目自动生效)
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
            task_type VARCHAR(16) NOT NULL CHECK (task_type IN (
                ''feat'', ''fix'', ''verify'', ''investigation'', ''drill'', ''docs'', ''ops'', ''deploy'',
                ''outline'', ''draft'', ''polish'', ''continuity'', ''lore''
            )),
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
                status != ''closed'' OR (commit_hash IS NOT NULL OR proof_link IS NOT NULL OR notes IS NOT NULL)
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

-- 2. 对所有存量业务项目 Schema 的 tasks 表平滑升级 CHECK 约束
DO $$
DECLARE
    r RECORD;
    c RECORD;
BEGIN
    FOR r IN SELECT schema_name FROM information_schema.schemata 
             WHERE schema_name NOT IN ('information_schema', 'pg_catalog', 'pg_toast', 'shared', 'public')
               AND schema_name NOT LIKE 'pg_%'
    LOOP
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = r.schema_name AND table_name = 'tasks') THEN
            -- 查找并删除所有针对 task_type 的旧 CHECK 约束
            FOR c IN 
                SELECT con.conname 
                FROM pg_constraint con
                JOIN pg_namespace nsp ON nsp.oid = con.connamespace
                JOIN pg_class cls ON cls.oid = con.conrelid
                JOIN pg_attribute att ON att.attrelid = cls.oid AND att.attnum = ANY(con.conkey)
                WHERE nsp.nspname = r.schema_name
                  AND cls.relname = 'tasks'
                  AND con.contype = 'c'
                  AND att.attname = 'task_type'
            LOOP
                EXECUTE format('ALTER TABLE %I.tasks DROP CONSTRAINT IF EXISTS %I;', r.schema_name, c.conname);
            END LOOP;

            -- 建立包含叙事创作类型的统一 CHECK 约束
            EXECUTE format('ALTER TABLE %I.tasks ADD CONSTRAINT chk_tasks_task_type CHECK (task_type IN (
                ''feat'', ''fix'', ''verify'', ''investigation'', ''drill'', ''docs'', ''ops'', ''deploy'',
                ''outline'', ''draft'', ''polish'', ''continuity'', ''lore''
            ));', r.schema_name);

            -- 平滑更新 chk_task_closed_proof 允许 notes 闭环
            EXECUTE format('ALTER TABLE %I.tasks DROP CONSTRAINT IF EXISTS chk_task_closed_proof;', r.schema_name);
            EXECUTE format('ALTER TABLE %I.tasks ADD CONSTRAINT chk_task_closed_proof CHECK (
                status != ''closed'' OR (commit_hash IS NOT NULL OR proof_link IS NOT NULL OR notes IS NOT NULL)
            );', r.schema_name);
        END IF;
    END LOOP;
END $$;
