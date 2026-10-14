-- ============================================================================
-- Logbook 0.5.0: 多用户分组权限层 (09_multi_user_acl.sql)
-- 语义 (方案 A): 组内互见互写, 跨组不可见; 未挂组项目保持 legacy 全通兼容;
--               admin (shared.principals.is_admin) 全量可见。
-- 幂等执行: IF NOT EXISTS / DROP POLICY IF EXISTS / DO 块守卫。
-- 注意: PG 角色 (logbook_authenticated / lb_u_* / lb_g_*) 的创建与 GRANT 同步
--       由应用层 db.ensure_acl_bootstrap()/ensure_principal() 幂等执行,
--       因其依赖执行者当前用户权限, 不宜写死于迁移文件。
-- ============================================================================

-- 1. 账户注册表 (principals): logbook 账户 = MCP 可信身份 (LOGBOOK_AGENT_ID / SSH principal)
CREATE TABLE IF NOT EXISTS shared.principals (
    agent_name VARCHAR(64) PRIMARY KEY,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 2. 群组 (groups)
CREATE TABLE IF NOT EXISTS shared.groups (
    id BIGSERIAL PRIMARY KEY,
    group_name VARCHAR(64) NOT NULL UNIQUE,
    description TEXT,
    created_by VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- 3. 群组成员关系 (principal_groups)
CREATE TABLE IF NOT EXISTS shared.principal_groups (
    agent_name VARCHAR(64) NOT NULL REFERENCES shared.principals(agent_name) ON DELETE CASCADE,
    group_id BIGINT NOT NULL REFERENCES shared.groups(id) ON DELETE CASCADE,
    granted_by VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (agent_name, group_id)
);
CREATE INDEX IF NOT EXISTS idx_principal_groups_group ON shared.principal_groups(group_id);

-- 4. 项目-群组挂载 (project_groups): 挂载即收口 —— 项目一旦挂组,
--    未挂组用户立即不可见不可写 (RLS 函数 + 项目 schema GRANT 收回双保险)。
CREATE TABLE IF NOT EXISTS shared.project_groups (
    schema_name VARCHAR(32) NOT NULL,
    group_id BIGINT NOT NULL REFERENCES shared.groups(id) ON DELETE CASCADE,
    granted_by VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (schema_name, group_id)
);
CREATE INDEX IF NOT EXISTS idx_project_groups_group ON shared.project_groups(group_id);

-- ============================================================================
-- 5. ACL 裁决函数 (SECURITY DEFINER: 以迁移执行者身份读取成员关系表,
--    普通账户无需 principals/groups 直读权限)
-- ============================================================================

-- 当前会话可信身份 (数据面连接池 init 钩子注入; 未注入 = 系统面/legacy, 由表 owner 天然绕过 RLS)
CREATE OR REPLACE FUNCTION shared.acl_current_agent() RETURNS text
LANGUAGE sql STABLE AS $$
    SELECT COALESCE(current_setting('logbook.agent', true), '');
$$;

-- 当前身份是否 admin
CREATE OR REPLACE FUNCTION shared.acl_is_admin() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = shared, pg_temp AS $$
    SELECT EXISTS (
        SELECT 1 FROM shared.principals
        WHERE agent_name = shared.acl_current_agent() AND is_admin
    );
$$;

-- 项目 (schema) 对身份是否可见: admin / 未挂组(legacy 全通) / 指定身份所在群组已挂载
-- p_agent 缺省时取当前会话身份 (RLS 策略路径); 显式传入供系统面代查 (probe_project)。
CREATE OR REPLACE FUNCTION shared.acl_project_visible(p_schema text, p_agent text DEFAULT NULL) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = shared, pg_temp AS $$
    WITH a AS (SELECT COALESCE(p_agent, shared.acl_current_agent()) AS agent)
    SELECT EXISTS (
                SELECT 1 FROM a JOIN shared.principals pr ON pr.agent_name = a.agent WHERE pr.is_admin
            )
        OR NOT EXISTS (SELECT 1 FROM shared.project_groups WHERE schema_name = p_schema)
        OR EXISTS (
            SELECT 1
            FROM shared.project_groups g
            JOIN shared.principal_groups m ON m.group_id = g.group_id
            JOIN a ON a.agent = m.agent_name
            WHERE g.schema_name = p_schema
        );
$$;

-- 收件人是否已注册账户 (供信箱退信校验, 无需暴露 principals 全表)
CREATE OR REPLACE FUNCTION shared.acl_principal_exists(p_name text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = shared, pg_temp AS $$
    SELECT EXISTS (SELECT 1 FROM shared.principals WHERE agent_name = p_name);
$$;
-- 注: acl_mailbox_has_room 依赖迁移 10 新增的 is_archived 列, 定义见 10_mailbox_completion.sql。

-- ============================================================================
-- 6. 行级安全 (RLS): 项目作用域数据对跨组身份硬不可见。
--    表 owner (登录角色/系统面) 默认豁免 RLS, 存量单用户部署行为零变化。
-- ============================================================================

-- 6.1 项目注册表: 不可见项目的元数据行对普通账户隐藏
ALTER TABLE shared.projects ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_projects_visible ON shared.projects;
CREATE POLICY p_projects_visible ON shared.projects
    FOR SELECT
    USING (shared.acl_project_visible(schema_name));

-- 6.2 排查手记: 跨组不可见; public_safe 密级保持全局开源可见
ALTER TABLE shared.devlogs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_devlogs_select ON shared.devlogs;
DROP POLICY IF EXISTS p_devlogs_insert ON shared.devlogs;
DROP POLICY IF EXISTS p_devlogs_update ON shared.devlogs;
CREATE POLICY p_devlogs_select ON shared.devlogs
    FOR SELECT
    USING (shared.acl_project_visible(project_id) OR visibility = 'public_safe');
CREATE POLICY p_devlogs_insert ON shared.devlogs
    FOR INSERT
    WITH CHECK (shared.acl_project_visible(project_id));
CREATE POLICY p_devlogs_update ON shared.devlogs
    FOR UPDATE
    USING (shared.acl_project_visible(project_id))
    WITH CHECK (shared.acl_project_visible(project_id));

-- 6.3 研发调研知识库: 同手记密级防线
ALTER TABLE shared.researches ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_researches_select ON shared.researches;
DROP POLICY IF EXISTS p_researches_insert ON shared.researches;
DROP POLICY IF EXISTS p_researches_update ON shared.researches;
CREATE POLICY p_researches_select ON shared.researches
    FOR SELECT
    USING (shared.acl_project_visible(project_id) OR visibility = 'public_safe');
CREATE POLICY p_researches_insert ON shared.researches
    FOR INSERT
    WITH CHECK (shared.acl_project_visible(project_id));
CREATE POLICY p_researches_update ON shared.researches
    FOR UPDATE
    USING (shared.acl_project_visible(project_id))
    WITH CHECK (shared.acl_project_visible(project_id));

-- 6.4 文件租约: 全操作按项目可见性收口
ALTER TABLE shared.file_leases ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_file_leases_all ON shared.file_leases;
CREATE POLICY p_file_leases_all ON shared.file_leases
    FOR ALL
    USING (shared.acl_project_visible(project_id))
    WITH CHECK (shared.acl_project_visible(project_id));
