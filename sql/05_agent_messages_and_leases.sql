-- ============================================================================
-- Logbook 0.3.0: 多 Agent 对讲信箱、代码文件租约与验收流转平滑迁移脚本 (05_agent_messages_and_leases.sql)
-- 幂等执行: 采用 IF NOT EXISTS 与动态 Schema 遍历
-- ============================================================================

-- 1. 全局共享面：独立信箱消息表 (Agent Messages)
CREATE TABLE IF NOT EXISTS shared.agent_messages (
    id BIGSERIAL PRIMARY KEY,
    project_id VARCHAR(32) NOT NULL,
    from_agent VARCHAR(64) NOT NULL,
    to_agent VARCHAR(64) NOT NULL,
    subject VARCHAR(256) NOT NULL,
    content TEXT NOT NULL,
    task_id VARCHAR(32),
    thread_id VARCHAR(64),
    is_read BOOLEAN NOT NULL DEFAULT FALSE,
    read_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_agent_messages_inbox
    ON shared.agent_messages (project_id, to_agent, is_read, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_messages_task
    ON shared.agent_messages (project_id, task_id);
CREATE INDEX IF NOT EXISTS idx_agent_messages_thread
    ON shared.agent_messages (project_id, thread_id);

-- 2. 全局共享面：代码文件防冲突租约软锁表 (File Leases)
CREATE TABLE IF NOT EXISTS shared.file_leases (
    id BIGSERIAL PRIMARY KEY,
    project_id VARCHAR(32) NOT NULL,
    agent_name VARCHAR(64) NOT NULL,
    file_path VARCHAR(512) NOT NULL,
    lease_expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_project_file_lease UNIQUE (project_id, file_path)
);

CREATE INDEX IF NOT EXISTS idx_file_leases_lookup
    ON shared.file_leases (project_id, lease_expires_at);

-- 3. 动态项目执行面：为各项目 tasks 表追加 reviewer 字段并扩容 status 约束
DO $$
DECLARE
    r RECORD;
    s_name TEXT;
    c_name TEXT;
BEGIN
    FOR r IN SELECT schema_name FROM information_schema.schemata 
             WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'public', 'shared')
    LOOP
        s_name := r.schema_name;
        
        -- 追加 reviewer 字段 (默认由 zcode 验收)
        EXECUTE format('ALTER TABLE %I.tasks ADD COLUMN IF NOT EXISTS reviewer VARCHAR(64) DEFAULT ''zcode'';', s_name);
        
        -- 平滑更新 status 检查约束，追加 'review' 待验收态
        SELECT conname INTO c_name
        FROM pg_constraint c
        JOIN pg_namespace n ON n.oid = c.connamespace
        JOIN pg_class cl ON cl.oid = c.conrelid
        WHERE n.nspname = s_name AND cl.relname = 'tasks' AND c.contype = 'c' 
          AND pg_get_constraintdef(c.oid) LIKE '%status%IN%';
          
        IF c_name IS NOT NULL THEN
            EXECUTE format('ALTER TABLE %I.tasks DROP CONSTRAINT %I;', s_name, c_name);
        END IF;
        
        EXECUTE format('ALTER TABLE %I.tasks ADD CONSTRAINT chk_%s_task_status CHECK (status IN (''planned'', ''running'', ''review'', ''blocked'', ''closed'', ''wontfix''));', s_name, s_name);
    END LOOP;
END $$;
