-- ============================================================================
-- Logbook 0.6.1: 事实双表引擎写穿透加固、统一时间线与全量兼容触发器
-- 文件: sql/16_unify_fact_triggers_and_timeline.sql
-- 目标:
-- 1. 在 shared.records 建立联合物理唯一索引 (project_id, dataset_code, doc_no)
-- 2. 建立统一时间线表 shared.task_timeline 并安全导入 21 个 Schema 存量 500 条时间线数据
-- 3. 升级 shared.v_* 正典视图 (COALESCE id 防 NULL) 与投影视图
-- 4. 升级 shared.init_project_schema 模板函数，确保新建项目统一走事实视图架构
-- 5. 为 8 大正典视图及 21 个 Schema 同名视图挂载 INSTEAD OF 触发器，实现透明 SQL 写穿透
-- 幂等设计，带事务保护。
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 一、 物理联合唯一约束 (支持 ON CONFLICT (project_id, dataset_code, doc_no))
-- ----------------------------------------------------------------------------

CREATE UNIQUE INDEX IF NOT EXISTS uq_records_project_dataset_doc 
ON shared.records (project_id, dataset_code, doc_no);

-- 注册 sys_timeline 系统数据集字典 (若未存在)
INSERT INTO shared.datasets (code, name, category, field_schema, description)
VALUES (
    'sys_timeline', '任务状态演进时间线', 'system', '[
        {"key": "task_id", "label": "任务编号", "type": "string"},
        {"key": "from_status", "label": "原状态", "type": "string"},
        {"key": "to_status", "label": "新状态", "type": "string"},
        {"key": "operator", "label": "操作人", "type": "string"},
        {"key": "agent_ip", "label": "操作节点IP", "type": "string"},
        {"key": "remark", "label": "备注说明", "type": "string"},
        {"key": "occurred_at", "label": "发生时刻", "type": "string"}
    ]'::jsonb, '研发任务状态流转全生命周期精确审计轨迹'
)
ON CONFLICT (code) DO UPDATE SET 
    name = EXCLUDED.name,
    field_schema = EXCLUDED.field_schema,
    updated_at = CURRENT_TIMESTAMP;

-- ----------------------------------------------------------------------------
-- 二、 统一时间线表 shared.task_timeline 与存量数据安全归集
-- ----------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS shared.task_timeline (
    id          BIGSERIAL PRIMARY KEY,
    project_id  VARCHAR(64) NOT NULL,
    task_id     VARCHAR(32) NOT NULL,
    from_status VARCHAR(16),
    to_status   VARCHAR(16) NOT NULL,
    operator    VARCHAR(64) NOT NULL DEFAULT 'agy',
    agent_ip    VARCHAR(45) NOT NULL DEFAULT '0.0.0.0',
    remark      TEXT,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_shared_timeline_scope 
ON shared.task_timeline (project_id, task_id, occurred_at);

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'logbook_authenticated') THEN
        GRANT SELECT, INSERT, UPDATE ON shared.task_timeline TO logbook_authenticated;
        GRANT USAGE, SELECT ON SEQUENCE shared.task_timeline_id_seq TO logbook_authenticated;
    END IF;
END $$;

-- ----------------------------------------------------------------------------
-- 三、 升级 8 大正典直读视图 (shared.v_*) 保证 ID 永不为 NULL
-- ----------------------------------------------------------------------------

CREATE OR REPLACE VIEW shared.v_tasks AS
SELECT 
    doc_no AS id,
    payload ->> 'batch_id' AS batch_id,
    payload ->> 'parent_id' AS parent_id,
    COALESCE(payload ->> 'title', entity_name) AS title,
    payload ->> 'task_type' AS task_type,
    payload ->> 'priority' AS priority,
    status,
    payload ->> 'assignee' AS assignee,
    payload ->> 'reviewer' AS reviewer,
    COALESCE(payload ->> 'agent_ip', '0.0.0.0') AS agent_ip,
    payload ->> 'commit_hash' AS commit_hash,
    payload ->> 'proof_link' AS proof_link,
    payload ->> 'notes' AS notes,
    COALESCE(ARRAY(
        SELECT jsonb_array_elements_text(
            CASE WHEN jsonb_typeof(records.payload -> 'tags') = 'array' 
                 THEN records.payload -> 'tags' 
                 ELSE '[]'::jsonb END
        )
    ), '{}'::text[]) AS tags,
    created_at,
    updated_at,
    (payload ->> 'started_at')::timestamptz AS started_at,
    (payload ->> 'closed_at')::timestamptz AS closed_at,
    (payload ->> 'duration_seconds')::integer AS duration_seconds,
    project_id,
    id AS record_id
FROM shared.records
WHERE dataset_code = 'sys_task';

CREATE OR REPLACE VIEW shared.v_findings AS
SELECT 
    doc_no AS id,
    payload ->> 'source' AS source,
    payload ->> 'severity' AS severity,
    status,
    payload ->> 'task_id' AS task_id,
    payload ->> 'reporter' AS reporter,
    COALESCE(payload ->> 'agent_ip', '0.0.0.0') AS agent_ip,
    COALESCE(payload ->> 'summary', entity_name) AS summary,
    payload ->> 'resolution' AS resolution,
    COALESCE((payload ->> 'discovered_at')::timestamptz, created_at) AS discovered_at,
    (payload ->> 'resolved_at')::timestamptz AS resolved_at,
    project_id,
    id AS record_id
FROM shared.records
WHERE dataset_code = 'sys_finding';

CREATE OR REPLACE VIEW shared.v_waitings AS
SELECT 
    doc_no AS id,
    payload ->> 'category' AS category,
    status,
    payload ->> 'owner' AS owner,
    COALESCE(payload ->> 'agent_ip', '0.0.0.0') AS agent_ip,
    COALESCE(payload ->> 'description', entity_name) AS description,
    payload ->> 'resolution' AS resolution,
    COALESCE((payload ->> 'blocked_at')::timestamptz, created_at) AS blocked_at,
    (payload ->> 'resolved_at')::timestamptz AS resolved_at,
    project_id,
    id AS record_id
FROM shared.records
WHERE dataset_code = 'sys_waiting';

CREATE OR REPLACE VIEW shared.v_batches AS
SELECT 
    doc_no AS id,
    COALESCE(payload ->> 'title', entity_name) AS title,
    status,
    payload ->> 'branch_name' AS branch_name,
    payload ->> 'summary' AS summary,
    payload ->> 'methodology_notes' AS methodology_notes,
    COALESCE(payload ->> 'agent_ip', '0.0.0.0') AS agent_ip,
    created_at,
    (payload ->> 'closed_at')::timestamptz AS closed_at,
    project_id,
    id AS record_id
FROM shared.records
WHERE dataset_code = 'sys_batch';

CREATE OR REPLACE VIEW shared.v_devlogs AS
SELECT 
    COALESCE((payload ->> 'devlog_id')::bigint, id) AS id,
    project_id,
    payload ->> 'task_id' AS task_id,
    COALESCE(payload ->> 'title', entity_name) AS title,
    payload ->> 'author' AS author,
    COALESCE(payload ->> 'agent_ip', '0.0.0.0') AS agent_ip,
    payload ->> 'problem' AS problem,
    payload ->> 'root_cause' AS root_cause,
    payload ->> 'solution' AS solution,
    payload ->> 'evidence' AS evidence,
    status AS visibility,
    COALESCE(ARRAY(
        SELECT jsonb_array_elements_text(
            CASE WHEN jsonb_typeof(records.payload -> 'tags') = 'array' 
                 THEN records.payload -> 'tags' 
                 ELSE '[]'::jsonb END
        )
    ), '{}'::text[]) AS tags,
    COALESCE((payload ->> 'occurred_at')::timestamptz, created_at) AS occurred_at,
    created_at,
    updated_at,
    embedding,
    tsv_content,
    id AS record_id
FROM shared.records
WHERE dataset_code = 'sys_devlog';

CREATE OR REPLACE VIEW shared.v_rules AS
SELECT 
    doc_no AS id,
    payload ->> 'category' AS category,
    COALESCE(payload ->> 'title', entity_name) AS title,
    payload ->> 'summary' AS summary,
    payload ->> 'bad_practice' AS bad_practice,
    payload ->> 'good_practice' AS good_practice,
    payload ->> 'constraints' AS constraints,
    created_at,
    updated_at,
    id AS record_id
FROM shared.records
WHERE dataset_code = 'sys_rule';

CREATE OR REPLACE VIEW shared.v_researches AS
SELECT 
    COALESCE((payload ->> 'research_id')::bigint, id) AS id,
    project_id,
    payload ->> 'task_id' AS task_id,
    payload ->> 'batch_id' AS batch_id,
    COALESCE(payload ->> 'title', entity_name) AS title,
    payload ->> 'category' AS category,
    payload ->> 'author' AS author,
    COALESCE(payload ->> 'agent_ip', '0.0.0.0') AS agent_ip,
    status,
    payload ->> 'objective' AS objective,
    payload ->> 'market_landscape' AS market_landscape,
    payload ->> 'tradeoffs' AS tradeoffs,
    payload ->> 'decision' AS decision,
    payload ->> 'references' AS "references",
    doc_no,
    payload ->> 'authority' AS authority,
    payload ->> 'legal_level' AS legal_level,
    (payload ->> 'effective_date')::date AS effective_date,
    COALESCE(payload ->> 'visibility', 'team') AS visibility,
    COALESCE(ARRAY(
        SELECT jsonb_array_elements_text(
            CASE WHEN jsonb_typeof(records.payload -> 'tags') = 'array' 
                 THEN records.payload -> 'tags' 
                 ELSE '[]'::jsonb END
        )
    ), '{}'::text[]) AS tags,
    embedding,
    tsv_content,
    created_at,
    updated_at,
    id AS record_id
FROM shared.records
WHERE dataset_code = 'sys_research';

CREATE OR REPLACE VIEW shared.v_agent_messages AS
SELECT 
    COALESCE((payload ->> 'message_id')::bigint, id) AS id,
    project_id,
    payload ->> 'from_agent' AS from_agent,
    COALESCE(payload ->> 'from_ip', '0.0.0.0') AS from_ip,
    payload ->> 'to_agent' AS to_agent,
    COALESCE(payload ->> 'to_ip', '0.0.0.0') AS to_ip,
    COALESCE(payload ->> 'subject', entity_name) AS subject,
    payload ->> 'content' AS content,
    payload ->> 'task_id' AS task_id,
    payload ->> 'thread_id' AS thread_id,
    COALESCE((payload ->> 'is_read')::boolean, status = 'read') AS is_read,
    (payload ->> 'read_at')::timestamptz AS read_at,
    payload ->> 'read_by_agent' AS read_by_agent,
    COALESCE((payload ->> 'is_archived')::boolean, status = 'archived') AS is_archived,
    (payload ->> 'archived_at')::timestamptz AS archived_at,
    created_at,
    id AS record_id
FROM shared.records
WHERE dataset_code = 'sys_message';

-- ----------------------------------------------------------------------------
-- 四、 升级 shared.init_project_schema 动态模板函数 (新项目统一事实引擎)
-- ----------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION shared.init_project_schema(p_name TEXT) 
RETURNS VOID AS $$
DECLARE
    schema_sql TEXT;
BEGIN
    schema_sql := format('
        CREATE SCHEMA IF NOT EXISTS %1$I;

        -- 建立 4 大投影视图与时间线视图
        CREATE OR REPLACE VIEW %1$I.tasks AS 
        SELECT * FROM shared.v_tasks WHERE project_id = %1$L;

        CREATE OR REPLACE VIEW %1$I.findings AS 
        SELECT * FROM shared.v_findings WHERE project_id = %1$L;

        CREATE OR REPLACE VIEW %1$I.waitings AS 
        SELECT * FROM shared.v_waitings WHERE project_id = %1$L;

        CREATE OR REPLACE VIEW %1$I.batches AS 
        SELECT * FROM shared.v_batches WHERE project_id = %1$L;

        CREATE OR REPLACE VIEW %1$I.task_timeline AS 
        SELECT id, task_id, from_status, to_status, operator, agent_ip, remark, occurred_at 
        FROM shared.task_timeline 
        WHERE project_id = %1$L;
    ', p_name);

    EXECUTE schema_sql;
END;
$$ LANGUAGE plpgsql;

-- ----------------------------------------------------------------------------
-- 五、 8 大正典直读视图挂载 INSTEAD OF 触发器函数 (纯 SQL 写穿透保底)
-- ----------------------------------------------------------------------------

-- 1. v_tasks 触发器
CREATE OR REPLACE FUNCTION shared.tg_v_tasks_write()
RETURNS TRIGGER AS $$
DECLARE
    v_tags jsonb := '[]'::jsonb;
BEGIN
    IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
        IF NEW.tags IS NOT NULL THEN
            v_tags := to_jsonb(NEW.tags);
        END IF;
        INSERT INTO shared.records (
            project_id, dataset_code, doc_no, entity_name, status, event_date,
            payload, created_at, updated_at
        ) VALUES (
            NEW.project_id, 'sys_task', NEW.id, LEFT(NEW.title, 255), NEW.status,
            COALESCE(NEW.created_at::date, CURRENT_DATE),
            jsonb_strip_nulls(jsonb_build_object(
                'title', NEW.title,
                'batch_id', NEW.batch_id,
                'parent_id', NEW.parent_id,
                'task_type', NEW.task_type,
                'priority', NEW.priority,
                'assignee', NEW.assignee,
                'reviewer', NEW.reviewer,
                'agent_ip', NEW.agent_ip,
                'commit_hash', NEW.commit_hash,
                'proof_link', NEW.proof_link,
                'notes', NEW.notes,
                'tags', v_tags,
                'started_at', NEW.started_at,
                'closed_at', NEW.closed_at,
                'duration_seconds', NEW.duration_seconds
            )),
            COALESCE(NEW.created_at, CURRENT_TIMESTAMP),
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (project_id, dataset_code, doc_no) DO UPDATE SET
            entity_name = EXCLUDED.entity_name,
            status = EXCLUDED.status,
            payload = EXCLUDED.payload,
            updated_at = CURRENT_TIMESTAMP;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        DELETE FROM shared.records 
        WHERE project_id = OLD.project_id AND dataset_code = 'sys_task' AND doc_no = OLD.id;
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_v_tasks_write ON shared.v_tasks;
CREATE TRIGGER trg_v_tasks_write
INSTEAD OF INSERT OR UPDATE OR DELETE ON shared.v_tasks
FOR EACH ROW EXECUTE FUNCTION shared.tg_v_tasks_write();

-- 2. v_findings 触发器
CREATE OR REPLACE FUNCTION shared.tg_v_findings_write()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
        INSERT INTO shared.records (
            project_id, dataset_code, doc_no, entity_name, status, event_date,
            payload, created_at, updated_at
        ) VALUES (
            NEW.project_id, 'sys_finding', NEW.id, LEFT(NEW.summary, 255), NEW.status,
            COALESCE(NEW.discovered_at::date, CURRENT_DATE),
            jsonb_strip_nulls(jsonb_build_object(
                'summary', NEW.summary,
                'source', NEW.source,
                'severity', NEW.severity,
                'task_id', NEW.task_id,
                'reporter', NEW.reporter,
                'agent_ip', NEW.agent_ip,
                'resolution', NEW.resolution,
                'discovered_at', NEW.discovered_at,
                'resolved_at', NEW.resolved_at
            )),
            COALESCE(NEW.discovered_at, CURRENT_TIMESTAMP),
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (project_id, dataset_code, doc_no) DO UPDATE SET
            entity_name = EXCLUDED.entity_name,
            status = EXCLUDED.status,
            payload = EXCLUDED.payload,
            updated_at = CURRENT_TIMESTAMP;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        DELETE FROM shared.records 
        WHERE project_id = OLD.project_id AND dataset_code = 'sys_finding' AND doc_no = OLD.id;
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_v_findings_write ON shared.v_findings;
CREATE TRIGGER trg_v_findings_write
INSTEAD OF INSERT OR UPDATE OR DELETE ON shared.v_findings
FOR EACH ROW EXECUTE FUNCTION shared.tg_v_findings_write();

-- 3. v_waitings 触发器
CREATE OR REPLACE FUNCTION shared.tg_v_waitings_write()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
        INSERT INTO shared.records (
            project_id, dataset_code, doc_no, entity_name, status, event_date,
            payload, created_at, updated_at
        ) VALUES (
            NEW.project_id, 'sys_waiting', NEW.id, LEFT(NEW.description, 255), NEW.status,
            COALESCE(NEW.blocked_at::date, CURRENT_DATE),
            jsonb_strip_nulls(jsonb_build_object(
                'description', NEW.description,
                'category', NEW.category,
                'owner', NEW.owner,
                'agent_ip', NEW.agent_ip,
                'resolution', NEW.resolution,
                'blocked_at', NEW.blocked_at,
                'resolved_at', NEW.resolved_at
            )),
            COALESCE(NEW.blocked_at, CURRENT_TIMESTAMP),
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (project_id, dataset_code, doc_no) DO UPDATE SET
            entity_name = EXCLUDED.entity_name,
            status = EXCLUDED.status,
            payload = EXCLUDED.payload,
            updated_at = CURRENT_TIMESTAMP;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        DELETE FROM shared.records 
        WHERE project_id = OLD.project_id AND dataset_code = 'sys_waiting' AND doc_no = OLD.id;
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_v_waitings_write ON shared.v_waitings;
CREATE TRIGGER trg_v_waitings_write
INSTEAD OF INSERT OR UPDATE OR DELETE ON shared.v_waitings
FOR EACH ROW EXECUTE FUNCTION shared.tg_v_waitings_write();

-- 4. v_batches 触发器
CREATE OR REPLACE FUNCTION shared.tg_v_batches_write()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
        INSERT INTO shared.records (
            project_id, dataset_code, doc_no, entity_name, status, event_date,
            payload, created_at, updated_at
        ) VALUES (
            NEW.project_id, 'sys_batch', NEW.id, LEFT(NEW.title, 255), NEW.status,
            COALESCE(NEW.created_at::date, CURRENT_DATE),
            jsonb_strip_nulls(jsonb_build_object(
                'title', NEW.title,
                'branch_name', NEW.branch_name,
                'summary', NEW.summary,
                'methodology_notes', NEW.methodology_notes,
                'agent_ip', NEW.agent_ip,
                'closed_at', NEW.closed_at
            )),
            COALESCE(NEW.created_at, CURRENT_TIMESTAMP),
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (project_id, dataset_code, doc_no) DO UPDATE SET
            entity_name = EXCLUDED.entity_name,
            status = EXCLUDED.status,
            payload = EXCLUDED.payload,
            updated_at = CURRENT_TIMESTAMP;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        DELETE FROM shared.records 
        WHERE project_id = OLD.project_id AND dataset_code = 'sys_batch' AND doc_no = OLD.id;
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_v_batches_write ON shared.v_batches;
CREATE TRIGGER trg_v_batches_write
INSTEAD OF INSERT OR UPDATE OR DELETE ON shared.v_batches
FOR EACH ROW EXECUTE FUNCTION shared.tg_v_batches_write();

-- 5. v_rules 触发器
CREATE OR REPLACE FUNCTION shared.tg_v_rules_write()
RETURNS TRIGGER AS $$
BEGIN
    IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
        INSERT INTO shared.records (
            project_id, dataset_code, doc_no, entity_name, status, event_date,
            payload, created_at, updated_at
        ) VALUES (
            'global', 'sys_rule', NEW.id, LEFT(NEW.title, 255), 'active',
            COALESCE(NEW.created_at::date, CURRENT_DATE),
            jsonb_strip_nulls(jsonb_build_object(
                'title', NEW.title,
                'category', NEW.category,
                'summary', NEW.summary,
                'bad_practice', NEW.bad_practice,
                'good_practice', NEW.good_practice,
                'constraints', NEW.constraints
            )),
            COALESCE(NEW.created_at, CURRENT_TIMESTAMP),
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (project_id, dataset_code, doc_no) DO UPDATE SET
            entity_name = EXCLUDED.entity_name,
            payload = EXCLUDED.payload,
            updated_at = CURRENT_TIMESTAMP;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        DELETE FROM shared.records 
        WHERE project_id = 'global' AND dataset_code = 'sys_rule' AND doc_no = OLD.id;
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_v_rules_write ON shared.v_rules;
CREATE TRIGGER trg_v_rules_write
INSTEAD OF INSERT OR UPDATE OR DELETE ON shared.v_rules
FOR EACH ROW EXECUTE FUNCTION shared.tg_v_rules_write();

-- 6. v_devlogs 触发器
CREATE OR REPLACE FUNCTION shared.tg_v_devlogs_write()
RETURNS TRIGGER AS $$
DECLARE
    v_doc_no text;
    v_devlog_id bigint;
    v_tags jsonb := '[]'::jsonb;
BEGIN
    IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
        v_devlog_id := NEW.id;
        IF v_devlog_id IS NULL THEN
            SELECT COALESCE(MAX((payload->>'devlog_id')::bigint), 0) + 1 INTO v_devlog_id
            FROM shared.records WHERE dataset_code = 'sys_devlog';
        END IF;
        v_doc_no := 'DEVLOG-' || v_devlog_id::text;
        IF NEW.tags IS NOT NULL THEN
            v_tags := to_jsonb(NEW.tags);
        END IF;

        INSERT INTO shared.records (
            project_id, dataset_code, doc_no, entity_name, status, event_date,
            payload, embedding, created_at, updated_at
        ) VALUES (
            NEW.project_id, 'sys_devlog', v_doc_no, LEFT(NEW.title, 255), 
            COALESCE(NEW.visibility, 'project_private'),
            COALESCE(NEW.occurred_at::date, CURRENT_DATE),
            jsonb_strip_nulls(jsonb_build_object(
                'devlog_id', v_devlog_id,
                'title', NEW.title,
                'task_id', NEW.task_id,
                'author', NEW.author,
                'agent_ip', NEW.agent_ip,
                'problem', NEW.problem,
                'root_cause', NEW.root_cause,
                'solution', NEW.solution,
                'evidence', NEW.evidence,
                'visibility', NEW.visibility,
                'tags', v_tags,
                'occurred_at', NEW.occurred_at
            )),
            NEW.embedding,
            COALESCE(NEW.created_at, CURRENT_TIMESTAMP),
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (project_id, dataset_code, doc_no) DO UPDATE SET
            entity_name = EXCLUDED.entity_name,
            status = EXCLUDED.status,
            payload = EXCLUDED.payload,
            embedding = COALESCE(EXCLUDED.embedding, shared.records.embedding),
            updated_at = CURRENT_TIMESTAMP;
        NEW.id := v_devlog_id;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        DELETE FROM shared.records 
        WHERE dataset_code = 'sys_devlog' 
          AND (doc_no = 'DEVLOG-' || OLD.id::text OR (payload->>'devlog_id')::bigint = OLD.id);
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_v_devlogs_write ON shared.v_devlogs;
CREATE TRIGGER trg_v_devlogs_write
INSTEAD OF INSERT OR UPDATE OR DELETE ON shared.v_devlogs
FOR EACH ROW EXECUTE FUNCTION shared.tg_v_devlogs_write();

-- 7. v_agent_messages 触发器
CREATE OR REPLACE FUNCTION shared.tg_v_agent_messages_write()
RETURNS TRIGGER AS $$
DECLARE
    v_msg_id bigint;
    v_doc_no text;
BEGIN
    IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
        v_msg_id := NEW.id;
        IF v_msg_id IS NULL THEN
            SELECT COALESCE(MAX((payload->>'message_id')::bigint), 0) + 1 INTO v_msg_id
            FROM shared.records WHERE dataset_code = 'sys_message';
        END IF;
        v_doc_no := 'MSG-' || v_msg_id::text;

        INSERT INTO shared.records (
            project_id, dataset_code, doc_no, entity_name, status, event_date,
            payload, created_at, updated_at
        ) VALUES (
            NEW.project_id, 'sys_message', v_doc_no, LEFT(NEW.subject, 255), 
            CASE WHEN NEW.is_archived THEN 'archived' WHEN NEW.is_read THEN 'read' ELSE 'unread' END,
            COALESCE(NEW.created_at::date, CURRENT_DATE),
            jsonb_strip_nulls(jsonb_build_object(
                'message_id', v_msg_id,
                'from_agent', NEW.from_agent,
                'from_ip', NEW.from_ip,
                'to_agent', NEW.to_agent,
                'to_ip', NEW.to_ip,
                'subject', NEW.subject,
                'content', NEW.content,
                'task_id', NEW.task_id,
                'thread_id', NEW.thread_id,
                'is_read', NEW.is_read,
                'read_at', NEW.read_at,
                'read_by_agent', NEW.read_by_agent,
                'is_archived', NEW.is_archived,
                'archived_at', NEW.archived_at
            )),
            COALESCE(NEW.created_at, CURRENT_TIMESTAMP),
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (project_id, dataset_code, doc_no) DO UPDATE SET
            entity_name = EXCLUDED.entity_name,
            status = EXCLUDED.status,
            payload = EXCLUDED.payload,
            updated_at = CURRENT_TIMESTAMP;
        NEW.id := v_msg_id;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        DELETE FROM shared.records 
        WHERE dataset_code = 'sys_message' 
          AND (doc_no = 'MSG-' || OLD.id::text OR (payload->>'message_id')::bigint = OLD.id);
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_v_agent_messages_write ON shared.v_agent_messages;
CREATE TRIGGER trg_v_agent_messages_write
INSTEAD OF INSERT OR UPDATE OR DELETE ON shared.v_agent_messages
FOR EACH ROW EXECUTE FUNCTION shared.tg_v_agent_messages_write();

-- 8. v_researches 触发器
CREATE OR REPLACE FUNCTION shared.tg_v_researches_write()
RETURNS TRIGGER AS $$
DECLARE
    v_res_id bigint;
    v_doc_no text;
    v_tags jsonb := '[]'::jsonb;
BEGIN
    IF TG_OP = 'INSERT' OR TG_OP = 'UPDATE' THEN
        v_res_id := NEW.id;
        IF v_res_id IS NULL THEN
            SELECT COALESCE(MAX((payload->>'research_id')::bigint), 0) + 1 INTO v_res_id
            FROM shared.records WHERE dataset_code = 'sys_research';
        END IF;
        v_doc_no := COALESCE(NULLIF(NEW.doc_no, ''), 'RES-' || v_res_id::text);
        IF NEW.tags IS NOT NULL THEN
            v_tags := to_jsonb(NEW.tags);
        END IF;

        INSERT INTO shared.records (
            project_id, dataset_code, doc_no, entity_name, status, event_date,
            payload, embedding, created_at, updated_at
        ) VALUES (
            NEW.project_id, 'sys_research', v_doc_no, LEFT(NEW.title, 255), 
            COALESCE(NEW.status, 'completed'),
            COALESCE(NEW.created_at::date, CURRENT_DATE),
            jsonb_strip_nulls(jsonb_build_object(
                'research_id', v_res_id,
                'title', NEW.title,
                'category', NEW.category,
                'author', NEW.author,
                'agent_ip', NEW.agent_ip,
                'objective', NEW.objective,
                'market_landscape', NEW.market_landscape,
                'tradeoffs', NEW.tradeoffs,
                'decision', NEW.decision,
                'references', NEW."references",
                'doc_no', v_doc_no,
                'authority', NEW.authority,
                'legal_level', NEW.legal_level,
                'effective_date', NEW.effective_date,
                'visibility', NEW.visibility,
                'tags', v_tags,
                'task_id', NEW.task_id,
                'batch_id', NEW.batch_id
            )),
            NEW.embedding,
            COALESCE(NEW.created_at, CURRENT_TIMESTAMP),
            CURRENT_TIMESTAMP
        )
        ON CONFLICT (project_id, dataset_code, doc_no) DO UPDATE SET
            entity_name = EXCLUDED.entity_name,
            status = EXCLUDED.status,
            payload = EXCLUDED.payload,
            embedding = COALESCE(EXCLUDED.embedding, shared.records.embedding),
            updated_at = CURRENT_TIMESTAMP;
        NEW.id := v_res_id;
        RETURN NEW;
    ELSIF TG_OP = 'DELETE' THEN
        DELETE FROM shared.records 
        WHERE dataset_code = 'sys_research' 
          AND (doc_no = OLD.doc_no OR (payload->>'research_id')::bigint = OLD.id);
        RETURN OLD;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_v_researches_write ON shared.v_researches;
CREATE TRIGGER trg_v_researches_write
INSTEAD OF INSERT OR UPDATE OR DELETE ON shared.v_researches
FOR EACH ROW EXECUTE FUNCTION shared.tg_v_researches_write();

COMMIT;
