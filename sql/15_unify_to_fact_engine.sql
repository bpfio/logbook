-- ============================================================================
-- Logbook 0.6.0: 全库向通用事实双表归一化原子迁移脚本
-- 文件: sql/15_unify_to_fact_engine.sql
-- 目标:
-- 1. 在 shared.datasets 注册 8 大系统级数据集元数据字典 (sys_*)
-- 2. 在单事务内，将存量 21 个 Schema 的任务、缺陷、待办、批次、手记、邮件、调研、铁律
--    原子转录映射至 shared.records (强脊梁 + 软肋骨 JSONB + 768维原生向量)
-- 3. 建立 8 个人类直读正典视图 (shared.v_*)
-- 4. 自动双向行数校验，若有不一致立即抛异常并原子 ROLLBACK
-- 5. 旧表非破坏性重命名为 _legacy_* 物理封存，并建立原名同名视图透明向下兼容，零数据丢失
-- 幂等设计，带严格事务保护。
-- ============================================================================

BEGIN;

-- ----------------------------------------------------------------------------
-- 一、 注册系统级数据集字典定义 (shared.datasets)
-- ----------------------------------------------------------------------------

INSERT INTO shared.datasets (code, name, category, field_schema, description)
VALUES 
    ('sys_task', '系统任务台账', 'system', '[
        {"key": "title", "label": "任务标题", "type": "string"},
        {"key": "batch_id", "label": "研发批次号", "type": "string"},
        {"key": "task_type", "label": "任务类型", "type": "string"},
        {"key": "priority", "label": "优先级", "type": "string"},
        {"key": "assignee", "label": "负责人", "type": "string"},
        {"key": "reviewer", "label": "验收人", "type": "string"},
        {"key": "commit_hash", "label": "提交Hash", "type": "string"},
        {"key": "proof_link", "label": "凭证指针", "type": "string"},
        {"key": "notes", "label": "备忘证据", "type": "string"},
        {"key": "tags", "label": "标签列表", "type": "array"}
    ]'::jsonb, '研发任务与生命周期管理正典台账')
ON CONFLICT (code) DO UPDATE SET 
    name = EXCLUDED.name,
    field_schema = EXCLUDED.field_schema,
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO shared.datasets (code, name, category, field_schema, description)
VALUES 
    ('sys_finding', '缺陷与审计发现', 'system', '[
        {"key": "summary", "label": "缺陷摘要", "type": "string"},
        {"key": "source", "label": "来源渠道", "type": "string"},
        {"key": "severity", "label": "严重级别", "type": "string"},
        {"key": "task_id", "label": "关联任务", "type": "string"},
        {"key": "reporter", "label": "上报主体", "type": "string"},
        {"key": "resolution", "label": "处置结论", "type": "string"}
    ]'::jsonb, '工程缺陷与系统性审计发现项跟踪')
ON CONFLICT (code) DO UPDATE SET 
    name = EXCLUDED.name,
    field_schema = EXCLUDED.field_schema,
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO shared.datasets (code, name, category, field_schema, description)
VALUES 
    ('sys_waiting', '阻塞待办与裁决', 'system', '[
        {"key": "description", "label": "待办描述", "type": "string"},
        {"key": "category", "label": "待办类别", "type": "string"},
        {"key": "owner", "label": "裁决主体", "type": "string"},
        {"key": "resolution", "label": "裁决记录", "type": "string"}
    ]'::jsonb, '待用户决策与外部阻塞事项')
ON CONFLICT (code) DO UPDATE SET 
    name = EXCLUDED.name,
    field_schema = EXCLUDED.field_schema,
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO shared.datasets (code, name, category, field_schema, description)
VALUES 
    ('sys_batch', '研发批次', 'system', '[
        {"key": "title", "label": "批次标题", "type": "string"},
        {"key": "branch_name", "label": "Git分支名", "type": "string"},
        {"key": "summary", "label": "批次综述", "type": "string"},
        {"key": "methodology_notes", "label": "方法论注记", "type": "string"}
    ]'::jsonb, '工程迭代与批次生命周期台账')
ON CONFLICT (code) DO UPDATE SET 
    name = EXCLUDED.name,
    field_schema = EXCLUDED.field_schema,
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO shared.datasets (code, name, category, field_schema, description)
VALUES 
    ('sys_devlog', '研发根因手记', 'system', '[
        {"key": "devlog_id", "label": "历史手记ID", "type": "number"},
        {"key": "title", "label": "手记标题", "type": "string"},
        {"key": "task_id", "label": "关联任务", "type": "string"},
        {"key": "problem", "label": "故障现象", "type": "string"},
        {"key": "root_cause", "label": "根因分析", "type": "string"},
        {"key": "solution", "label": "解决方案", "type": "string"},
        {"key": "evidence", "label": "验证证据", "type": "string"},
        {"key": "author", "label": "撰写者", "type": "string"}
    ]'::jsonb, '技术沉淀与长期语义记忆库 (含768维向量)')
ON CONFLICT (code) DO UPDATE SET 
    name = EXCLUDED.name,
    field_schema = EXCLUDED.field_schema,
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO shared.datasets (code, name, category, field_schema, description)
VALUES 
    ('sys_message', '团队协同对讲邮件', 'system', '[
        {"key": "message_id", "label": "历史消息ID", "type": "number"},
        {"key": "subject", "label": "主题", "type": "string"},
        {"key": "from_agent", "label": "发件人", "type": "string"},
        {"key": "to_agent", "label": "收件人", "type": "string"},
        {"key": "content", "label": "正文内容", "type": "string"},
        {"key": "thread_id", "label": "会话标识", "type": "string"}
    ]'::jsonb, '多Agent点对点异步留信与协作对讲')
ON CONFLICT (code) DO UPDATE SET 
    name = EXCLUDED.name,
    field_schema = EXCLUDED.field_schema,
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO shared.datasets (code, name, category, field_schema, description)
VALUES 
    ('sys_research', '调研与架构决策', 'system', '[
        {"key": "research_id", "label": "历史调研ID", "type": "number"},
        {"key": "title", "label": "调研标题", "type": "string"},
        {"key": "category", "label": "调研类别", "type": "string"},
        {"key": "objective", "label": "调研目的", "type": "string"},
        {"key": "tradeoffs", "label": "对抗代价与权衡", "type": "string"},
        {"key": "decision", "label": "选型决策", "type": "string"}
    ]'::jsonb, '宏观政策、技术选型与世界观考据知识库 (含768维向量)')
ON CONFLICT (code) DO UPDATE SET 
    name = EXCLUDED.name,
    field_schema = EXCLUDED.field_schema,
    updated_at = CURRENT_TIMESTAMP;

INSERT INTO shared.datasets (code, name, category, field_schema, description)
VALUES 
    ('sys_rule', '架构铁律与工程红线', 'system', '[
        {"key": "title", "label": "铁律标题", "type": "string"},
        {"key": "summary", "label": "核心原则", "type": "string"},
        {"key": "bad_practice", "label": "错误示范", "type": "string"},
        {"key": "good_practice", "label": "正确做法", "type": "string"},
        {"key": "constraints", "label": "边界约束", "type": "string"}
    ]'::jsonb, '跨项目统一架构铁律与团队单一事实源 (SSOT)')
ON CONFLICT (code) DO UPDATE SET 
    name = EXCLUDED.name,
    field_schema = EXCLUDED.field_schema,
    updated_at = CURRENT_TIMESTAMP;


-- ----------------------------------------------------------------------------
-- 二、 存量各 Schema 事项数据原子转录汇聚
-- ----------------------------------------------------------------------------

DO $$
DECLARE
    sch RECORD;
    has_tasks BOOLEAN;
    has_findings BOOLEAN;
    has_waitings BOOLEAN;
    has_batches BOOLEAN;
BEGIN
    FOR sch IN 
        SELECT schema_name 
        FROM information_schema.schemata 
        WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'public', 'shared')
    LOOP
        -- 1. 转录 tasks
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables 
            WHERE table_schema = sch.schema_name AND table_name = 'tasks'
        ) INTO has_tasks;

        IF has_tasks THEN
            EXECUTE format('
                INSERT INTO shared.records (
                    project_id, dataset_code, doc_no, entity_name, status,
                    event_date, payload, created_at, updated_at
                )
                SELECT 
                    %L, ''sys_task'', t.id, LEFT(t.title, 255), t.status,
                    COALESCE(t.started_at::date, t.created_at::date, CURRENT_DATE),
                    jsonb_strip_nulls(jsonb_build_object(
                        ''title'', t.title,
                        ''batch_id'', t.batch_id,
                        ''task_type'', t.task_type,
                        ''priority'', t.priority,
                        ''assignee'', t.assignee,
                        ''reviewer'', t.reviewer,
                        ''parent_id'', t.parent_id,
                        ''commit_hash'', t.commit_hash,
                        ''proof_link'', t.proof_link,
                        ''notes'', t.notes,
                        ''tags'', to_jsonb(t.tags),
                        ''started_at'', t.started_at,
                        ''closed_at'', t.closed_at,
                        ''duration_seconds'', t.duration_seconds,
                        ''agent_ip'', t.agent_ip
                    )),
                    t.created_at, t.updated_at
                FROM %I.tasks t
                WHERE NOT EXISTS (
                    SELECT 1 FROM shared.records r 
                    WHERE r.project_id = %L AND r.dataset_code = ''sys_task'' AND r.doc_no = t.id
                );
            ', sch.schema_name, sch.schema_name, sch.schema_name);
        END IF;

        -- 2. 转录 findings
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables 
            WHERE table_schema = sch.schema_name AND table_name = 'findings'
        ) INTO has_findings;

        IF has_findings THEN
            EXECUTE format('
                INSERT INTO shared.records (
                    project_id, dataset_code, doc_no, entity_name, status,
                    event_date, payload, created_at, updated_at
                )
                SELECT 
                    %L, ''sys_finding'', f.id, LEFT(f.summary, 255), f.status,
                    COALESCE(f.discovered_at::date, CURRENT_DATE),
                    jsonb_strip_nulls(jsonb_build_object(
                        ''summary'', f.summary,
                        ''source'', f.source,
                        ''severity'', f.severity,
                        ''task_id'', f.task_id,
                        ''reporter'', f.reporter,
                        ''resolution'', f.resolution,
                        ''discovered_at'', f.discovered_at,
                        ''resolved_at'', f.resolved_at,
                        ''agent_ip'', f.agent_ip
                    )),
                    f.discovered_at, COALESCE(f.resolved_at, f.discovered_at)
                FROM %I.findings f
                WHERE NOT EXISTS (
                    SELECT 1 FROM shared.records r 
                    WHERE r.project_id = %L AND r.dataset_code = ''sys_finding'' AND r.doc_no = f.id
                );
            ', sch.schema_name, sch.schema_name, sch.schema_name);
        END IF;

        -- 3. 转录 waitings
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables 
            WHERE table_schema = sch.schema_name AND table_name = 'waitings'
        ) INTO has_waitings;

        IF has_waitings THEN
            EXECUTE format('
                INSERT INTO shared.records (
                    project_id, dataset_code, doc_no, entity_name, status,
                    event_date, payload, created_at, updated_at
                )
                SELECT 
                    %L, ''sys_waiting'', w.id, LEFT(w.description, 255), w.status,
                    COALESCE(w.blocked_at::date, CURRENT_DATE),
                    jsonb_strip_nulls(jsonb_build_object(
                        ''description'', w.description,
                        ''category'', w.category,
                        ''owner'', w.owner,
                        ''resolution'', w.resolution,
                        ''blocked_at'', w.blocked_at,
                        ''resolved_at'', w.resolved_at,
                        ''agent_ip'', w.agent_ip
                    )),
                    w.blocked_at, COALESCE(w.resolved_at, w.blocked_at)
                FROM %I.waitings w
                WHERE NOT EXISTS (
                    SELECT 1 FROM shared.records r 
                    WHERE r.project_id = %L AND r.dataset_code = ''sys_waiting'' AND r.doc_no = w.id
                );
            ', sch.schema_name, sch.schema_name, sch.schema_name);
        END IF;

        -- 4. 转录 batches
        SELECT EXISTS (
            SELECT 1 FROM information_schema.tables 
            WHERE table_schema = sch.schema_name AND table_name = 'batches'
        ) INTO has_batches;

        IF has_batches THEN
            EXECUTE format('
                INSERT INTO shared.records (
                    project_id, dataset_code, doc_no, entity_name, status,
                    event_date, payload, created_at, updated_at
                )
                SELECT 
                    %L, ''sys_batch'', b.id, LEFT(COALESCE(b.title, b.summary, b.id), 255), b.status,
                    COALESCE(b.created_at::date, CURRENT_DATE),
                    jsonb_strip_nulls(jsonb_build_object(
                        ''title'', b.title,
                        ''branch_name'', b.branch_name,
                        ''summary'', b.summary,
                        ''methodology_notes'', b.methodology_notes,
                        ''closed_at'', b.closed_at,
                        ''agent_ip'', b.agent_ip
                    )),
                    b.created_at, COALESCE(b.closed_at, b.created_at)
                FROM %I.batches b
                WHERE NOT EXISTS (
                    SELECT 1 FROM shared.records r 
                    WHERE r.project_id = %L AND r.dataset_code = ''sys_batch'' AND r.doc_no = b.id
                );
            ', sch.schema_name, sch.schema_name, sch.schema_name);
        END IF;

    END LOOP;
END $$;


-- ----------------------------------------------------------------------------
-- 三、 shared 下手记、邮件、调研、铁律无损汇聚
-- ----------------------------------------------------------------------------

-- 1. 转录 shared.devlogs (保持 768 维星火向量索引)
INSERT INTO shared.records (
    project_id, dataset_code, doc_no, entity_name, status,
    event_date, payload, embedding, created_at, updated_at
)
SELECT 
    d.project_id, 'sys_devlog', 'DEVLOG-' || d.id::text, LEFT(d.title, 255), d.visibility,
    COALESCE(d.occurred_at::date, d.created_at::date, CURRENT_DATE),
    jsonb_strip_nulls(jsonb_build_object(
        'devlog_id', d.id,
        'title', d.title,
        'task_id', d.task_id,
        'problem', d.problem,
        'root_cause', d.root_cause,
        'solution', d.solution,
        'evidence', d.evidence,
        'visibility', d.visibility,
        'tags', to_jsonb(d.tags),
        'author', d.author,
        'agent_ip', d.agent_ip,
        'occurred_at', d.occurred_at
    )),
    d.embedding, d.created_at, d.updated_at
FROM shared.devlogs d
WHERE NOT EXISTS (
    SELECT 1 FROM shared.records r 
    WHERE r.project_id = d.project_id AND r.dataset_code = 'sys_devlog' AND r.doc_no = 'DEVLOG-' || d.id::text
);

-- 2. 转录 shared.agent_messages (团队协作信件)
INSERT INTO shared.records (
    project_id, dataset_code, doc_no, entity_name, status,
    event_date, payload, created_at, updated_at
)
SELECT 
    m.project_id, 'sys_message', 'MSG-' || m.id::text, LEFT(m.subject, 255), 
    CASE WHEN m.is_archived THEN 'archived' WHEN m.is_read THEN 'read' ELSE 'unread' END,
    COALESCE(m.created_at::date, CURRENT_DATE),
    jsonb_strip_nulls(jsonb_build_object(
        'message_id', m.id,
        'subject', m.subject,
        'from_agent', m.from_agent,
        'from_ip', m.from_ip,
        'to_agent', m.to_agent,
        'to_ip', m.to_ip,
        'content', m.content,
        'task_id', m.task_id,
        'thread_id', m.thread_id,
        'is_read', m.is_read,
        'read_at', m.read_at,
        'read_by_agent', m.read_by_agent,
        'is_archived', m.is_archived,
        'archived_at', m.archived_at
    )),
    m.created_at, COALESCE(m.read_at, m.created_at)
FROM shared.agent_messages m
WHERE NOT EXISTS (
    SELECT 1 FROM shared.records r 
    WHERE r.project_id = m.project_id AND r.dataset_code = 'sys_message' AND r.doc_no = 'MSG-' || m.id::text
);

-- 3. 转录 shared.researches (保持 768 维向量索引)
INSERT INTO shared.records (
    project_id, dataset_code, doc_no, entity_name, status,
    event_date, payload, embedding, created_at, updated_at
)
SELECT 
    r.project_id, 'sys_research', COALESCE(NULLIF(r.doc_no, ''), 'RES-' || r.id::text), LEFT(r.title, 255), r.status,
    COALESCE(r.created_at::date, CURRENT_DATE),
    jsonb_strip_nulls(jsonb_build_object(
        'research_id', r.id,
        'title', r.title,
        'task_id', r.task_id,
        'batch_id', r.batch_id,
        'category', r.category,
        'author', r.author,
        'agent_ip', r.agent_ip,
        'objective', r.objective,
        'market_landscape', r.market_landscape,
        'tradeoffs', r.tradeoffs,
        'decision', r.decision,
        'references', r.references,
        'visibility', r.visibility,
        'tags', to_jsonb(r.tags),
        'authority', r.authority,
        'legal_level', r.legal_level,
        'effective_date', r.effective_date
    )),
    r.embedding, r.created_at, r.updated_at
FROM shared.researches r
WHERE NOT EXISTS (
    SELECT 1 FROM shared.records r 
    WHERE r.project_id = r.project_id AND r.dataset_code = 'sys_research' AND r.doc_no = COALESCE(NULLIF(r.doc_no, ''), 'RES-' || r.id::text)
);

-- 4. 转录 shared.rules (统一架构铁律)
INSERT INTO shared.records (
    project_id, dataset_code, doc_no, entity_name, status,
    event_date, payload, created_at, updated_at
)
SELECT 
    'global', 'sys_rule', ru.id, LEFT(ru.title, 255), 'active',
    COALESCE(ru.created_at::date, CURRENT_DATE),
    jsonb_strip_nulls(jsonb_build_object(
        'title', ru.title,
        'summary', ru.summary,
        'bad_practice', ru.bad_practice,
        'good_practice', ru.good_practice,
        'constraints', ru.constraints,
        'category', ru.category
    )),
    ru.created_at, ru.updated_at
FROM shared.rules ru
WHERE NOT EXISTS (
    SELECT 1 FROM shared.records r 
    WHERE r.project_id = 'global' AND r.dataset_code = 'sys_rule' AND r.doc_no = ru.id
);


-- ----------------------------------------------------------------------------
-- 四、 建立 8 个人类直读正典兼容视图 (Database Views)
-- ----------------------------------------------------------------------------

CREATE OR REPLACE VIEW shared.v_tasks AS
SELECT 
    id AS record_id,
    project_id,
    doc_no AS id,
    COALESCE(payload->>'title', entity_name) AS title,
    status,
    (payload->>'priority') AS priority,
    (payload->>'task_type') AS task_type,
    (payload->>'assignee') AS assignee,
    (payload->>'reviewer') AS reviewer,
    (payload->>'commit_hash') AS commit_hash,
    (payload->>'proof_link') AS proof_link,
    (payload->>'notes') AS notes,
    (payload->>'batch_id') AS batch_id,
    (payload->>'parent_id') AS parent_id,
    (payload->>'agent_ip') AS agent_ip,
    created_at,
    updated_at
FROM shared.records
WHERE dataset_code = 'sys_task';

CREATE OR REPLACE VIEW shared.v_findings AS
SELECT 
    id AS record_id,
    project_id,
    doc_no AS id,
    COALESCE(payload->>'summary', entity_name) AS summary,
    status,
    (payload->>'severity') AS severity,
    (payload->>'source') AS source,
    (payload->>'reporter') AS reporter,
    (payload->>'task_id') AS task_id,
    (payload->>'resolution') AS resolution,
    (payload->>'agent_ip') AS agent_ip,
    created_at,
    updated_at
FROM shared.records
WHERE dataset_code = 'sys_finding';

CREATE OR REPLACE VIEW shared.v_waitings AS
SELECT 
    id AS record_id,
    project_id,
    doc_no AS id,
    COALESCE(payload->>'description', entity_name) AS description,
    status,
    (payload->>'category') AS category,
    (payload->>'owner') AS owner,
    (payload->>'resolution') AS resolution,
    (payload->>'agent_ip') AS agent_ip,
    created_at,
    updated_at
FROM shared.records
WHERE dataset_code = 'sys_waiting';

CREATE OR REPLACE VIEW shared.v_batches AS
SELECT 
    id AS record_id,
    project_id,
    doc_no AS id,
    COALESCE(payload->>'title', entity_name) AS title,
    status,
    (payload->>'branch_name') AS branch_name,
    (payload->>'summary') AS summary,
    (payload->>'methodology_notes') AS methodology_notes,
    (payload->>'agent_ip') AS agent_ip,
    created_at,
    updated_at
FROM shared.records
WHERE dataset_code = 'sys_batch';

CREATE OR REPLACE VIEW shared.v_devlogs AS
SELECT 
    id AS record_id,
    project_id,
    ((payload->>'devlog_id')::bigint) AS id,
    doc_no,
    COALESCE(payload->>'title', entity_name) AS title,
    (payload->>'problem') AS problem,
    (payload->>'root_cause') AS root_cause,
    (payload->>'solution') AS solution,
    (payload->>'evidence') AS evidence,
    status AS visibility,
    (payload->>'author') AS author,
    (payload->>'task_id') AS task_id,
    (payload->>'agent_ip') AS agent_ip,
    embedding,
    tsv_content,
    created_at,
    updated_at
FROM shared.records
WHERE dataset_code = 'sys_devlog';

CREATE OR REPLACE VIEW shared.v_agent_messages AS
SELECT 
    id AS record_id,
    project_id,
    ((payload->>'message_id')::bigint) AS id,
    doc_no,
    COALESCE(payload->>'subject', entity_name) AS subject,
    (payload->>'from_agent') AS from_agent,
    (payload->>'from_ip') AS from_ip,
    (payload->>'to_agent') AS to_agent,
    (payload->>'to_ip') AS to_ip,
    (payload->>'content') AS content,
    (payload->>'task_id') AS task_id,
    (payload->>'thread_id') AS thread_id,
    status,
    created_at,
    updated_at
FROM shared.records
WHERE dataset_code = 'sys_message';

CREATE OR REPLACE VIEW shared.v_researches AS
SELECT 
    id AS record_id,
    project_id,
    ((payload->>'research_id')::bigint) AS id,
    doc_no,
    COALESCE(payload->>'title', entity_name) AS title,
    (payload->>'category') AS category,
    (payload->>'objective') AS objective,
    (payload->>'tradeoffs') AS tradeoffs,
    (payload->>'decision') AS decision,
    (payload->>'market_landscape') AS market_landscape,
    (payload->>'references') AS "references",
    status,
    (payload->>'visibility') AS visibility,
    (payload->>'author') AS author,
    (payload->>'task_id') AS task_id,
    (payload->>'batch_id') AS batch_id,
    (payload->>'agent_ip') AS agent_ip,
    embedding,
    tsv_content,
    created_at,
    updated_at
FROM shared.records
WHERE dataset_code = 'sys_research';

CREATE OR REPLACE VIEW shared.v_rules AS
SELECT 
    id AS record_id,
    doc_no AS id,
    COALESCE(payload->>'title', entity_name) AS title,
    (payload->>'category') AS category,
    (payload->>'summary') AS summary,
    (payload->>'bad_practice') AS bad_practice,
    (payload->>'good_practice') AS good_practice,
    (payload->>'constraints') AS constraints,
    status,
    created_at,
    updated_at
FROM shared.records
WHERE dataset_code = 'sys_rule';


-- ----------------------------------------------------------------------------
-- 五、 自动双向 Checksum 与 8 大实体行数一致性审计断言 (失败自动 ROLLBACK)
-- ----------------------------------------------------------------------------

DO $$
DECLARE
    sch RECORD;
    orig_tasks BIGINT := 0;
    orig_findings BIGINT := 0;
    orig_waitings BIGINT := 0;
    orig_batches BIGINT := 0;
    orig_devlogs BIGINT := 0;
    orig_messages BIGINT := 0;
    orig_researches BIGINT := 0;
    orig_rules BIGINT := 0;

    new_tasks BIGINT := 0;
    new_findings BIGINT := 0;
    new_waitings BIGINT := 0;
    new_batches BIGINT := 0;
    new_devlogs BIGINT := 0;
    new_messages BIGINT := 0;
    new_researches BIGINT := 0;
    new_rules BIGINT := 0;

    cnt BIGINT;
    has_t BOOLEAN;
BEGIN
    -- 1. 统计旧表行数
    FOR sch IN 
        SELECT schema_name 
        FROM information_schema.schemata 
        WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'public', 'shared')
    LOOP
        SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = sch.schema_name AND table_name = 'tasks') INTO has_t;
        IF has_t THEN
            EXECUTE format('SELECT count(*) FROM %I.tasks', sch.schema_name) INTO cnt;
            orig_tasks := orig_tasks + cnt;
        END IF;

        SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = sch.schema_name AND table_name = 'findings') INTO has_t;
        IF has_t THEN
            EXECUTE format('SELECT count(*) FROM %I.findings', sch.schema_name) INTO cnt;
            orig_findings := orig_findings + cnt;
        END IF;

        SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = sch.schema_name AND table_name = 'waitings') INTO has_t;
        IF has_t THEN
            EXECUTE format('SELECT count(*) FROM %I.waitings', sch.schema_name) INTO cnt;
            orig_waitings := orig_waitings + cnt;
        END IF;

        SELECT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = sch.schema_name AND table_name = 'batches') INTO has_t;
        IF has_t THEN
            EXECUTE format('SELECT count(*) FROM %I.batches', sch.schema_name) INTO cnt;
            orig_batches := orig_batches + cnt;
        END IF;
    END LOOP;

    SELECT count(*) FROM shared.devlogs INTO orig_devlogs;
    SELECT count(*) FROM shared.agent_messages INTO orig_messages;
    SELECT count(*) FROM shared.researches INTO orig_researches;
    SELECT count(*) FROM shared.rules INTO orig_rules;

    -- 2. 统计新转录记录行数
    SELECT count(*) FROM shared.records WHERE dataset_code = 'sys_task' INTO new_tasks;
    SELECT count(*) FROM shared.records WHERE dataset_code = 'sys_finding' INTO new_findings;
    SELECT count(*) FROM shared.records WHERE dataset_code = 'sys_waiting' INTO new_waitings;
    SELECT count(*) FROM shared.records WHERE dataset_code = 'sys_batch' INTO new_batches;
    SELECT count(*) FROM shared.records WHERE dataset_code = 'sys_devlog' INTO new_devlogs;
    SELECT count(*) FROM shared.records WHERE dataset_code = 'sys_message' INTO new_messages;
    SELECT count(*) FROM shared.records WHERE dataset_code = 'sys_research' INTO new_researches;
    SELECT count(*) FROM shared.records WHERE dataset_code = 'sys_rule' INTO new_rules;

    -- 3. 严格断言比对
    IF orig_tasks <> new_tasks THEN
        RAISE EXCEPTION 'TASK 行数校验失败: 原表 % 行 vs 事实表 % 行，触发事务安全回滚！', orig_tasks, new_tasks;
    END IF;

    IF orig_findings <> new_findings THEN
        RAISE EXCEPTION 'FINDING 行数校验失败: 原表 % 行 vs 事实表 % 行，触发事务安全回滚！', orig_findings, new_findings;
    END IF;

    IF orig_waitings <> new_waitings THEN
        RAISE EXCEPTION 'WAITING 行数校验失败: 原表 % 行 vs 事实表 % 行，触发事务安全回滚！', orig_waitings, new_waitings;
    END IF;

    IF orig_batches <> new_batches THEN
        RAISE EXCEPTION 'BATCH 行数校验失败: 原表 % 行 vs 事实表 % 行，触发事务安全回滚！', orig_batches, new_batches;
    END IF;

    IF orig_devlogs <> new_devlogs THEN
        RAISE EXCEPTION 'DEVLOG 行数校验失败: 原表 % 行 vs 事实表 % 行，触发事务安全回滚！', orig_devlogs, new_devlogs;
    END IF;

    IF orig_messages <> new_messages THEN
        RAISE EXCEPTION 'MESSAGE 行数校验失败: 原表 % 行 vs 事实表 % 行，触发事务安全回滚！', orig_messages, new_messages;
    END IF;

    IF orig_researches <> new_researches THEN
        RAISE EXCEPTION 'RESEARCH 行数校验失败: 原表 % 行 vs 事实表 % 行，触发事务安全回滚！', orig_researches, new_researches;
    END IF;

    IF orig_rules <> new_rules THEN
        RAISE EXCEPTION 'RULE 行数校验失败: 原表 % 行 vs 事实表 % 行，触发事务安全回滚！', orig_rules, new_rules;
    END IF;

    RAISE NOTICE '【校验成功】全部 8 大实体转录行数 100%% 绝对吻合: tasks=%, findings=%, waitings=%, batches=%, devlogs=%, messages=%, researches=%, rules=%',
        new_tasks, new_findings, new_waitings, new_batches, new_devlogs, new_messages, new_researches, new_rules;
END $$;


-- ----------------------------------------------------------------------------
-- 六、 旧表非破坏性重命名物理封存 (_legacy_*) 与同名兼容视图构建
-- ----------------------------------------------------------------------------

DO $$
DECLARE
    sch RECORD;
    tbl TEXT;
BEGIN
    -- 1. 重命名 21 个业务 Schema 下的旧表，并建立同名向下兼容视图
    FOR sch IN 
        SELECT schema_name 
        FROM information_schema.schemata 
        WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'public', 'shared')
    LOOP
        -- tasks
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = sch.schema_name AND table_name = 'tasks') THEN
            EXECUTE format('ALTER TABLE %I.tasks RENAME TO _legacy_tasks', sch.schema_name);
            EXECUTE format('CREATE OR REPLACE VIEW %I.tasks AS SELECT * FROM shared.v_tasks WHERE project_id = %L', sch.schema_name, sch.schema_name);
        END IF;

        -- findings
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = sch.schema_name AND table_name = 'findings') THEN
            EXECUTE format('ALTER TABLE %I.findings RENAME TO _legacy_findings', sch.schema_name);
            EXECUTE format('CREATE OR REPLACE VIEW %I.findings AS SELECT * FROM shared.v_findings WHERE project_id = %L', sch.schema_name, sch.schema_name);
        END IF;

        -- waitings
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = sch.schema_name AND table_name = 'waitings') THEN
            EXECUTE format('ALTER TABLE %I.waitings RENAME TO _legacy_waitings', sch.schema_name);
            EXECUTE format('CREATE OR REPLACE VIEW %I.waitings AS SELECT * FROM shared.v_waitings WHERE project_id = %L', sch.schema_name, sch.schema_name);
        END IF;

        -- batches
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = sch.schema_name AND table_name = 'batches') THEN
            EXECUTE format('ALTER TABLE %I.batches RENAME TO _legacy_batches', sch.schema_name);
            EXECUTE format('CREATE OR REPLACE VIEW %I.batches AS SELECT * FROM shared.v_batches WHERE project_id = %L', sch.schema_name, sch.schema_name);
        END IF;

        -- task_timeline (如有)
        IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = sch.schema_name AND table_name = 'task_timeline') THEN
            EXECUTE format('ALTER TABLE %I.task_timeline RENAME TO _legacy_task_timeline', sch.schema_name);
        END IF;
    END LOOP;

    -- 2. 重命名 shared 下旧单据表，并建立同名向下兼容视图
    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'shared' AND table_name = 'devlogs') THEN
        ALTER TABLE shared.devlogs RENAME TO _legacy_devlogs;
        CREATE OR REPLACE VIEW shared.devlogs AS SELECT * FROM shared.v_devlogs;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'shared' AND table_name = 'agent_messages') THEN
        ALTER TABLE shared.agent_messages RENAME TO _legacy_agent_messages;
        CREATE OR REPLACE VIEW shared.agent_messages AS SELECT * FROM shared.v_agent_messages;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'shared' AND table_name = 'researches') THEN
        ALTER TABLE shared.researches RENAME TO _legacy_researches;
        CREATE OR REPLACE VIEW shared.researches AS SELECT * FROM shared.v_researches;
    END IF;

    IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_schema = 'shared' AND table_name = 'rules') THEN
        ALTER TABLE shared.rules RENAME TO _legacy_rules;
        CREATE OR REPLACE VIEW shared.rules AS SELECT * FROM shared.v_rules;
    END IF;
END $$;

COMMIT;
