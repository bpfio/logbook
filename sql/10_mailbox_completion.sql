-- ============================================================================
-- Logbook 0.5.0: 站内邮箱完成面 (10_mailbox_completion.sql)
-- - 归档态: is_archived / archived_at (收件人个人信箱视图, 不影响对方)
-- - 收件人余量函数 acl_mailbox_has_room (防灌爆, GUC logbook.mailbox_cap 可覆盖, 缺省 500)
-- - 信箱 RLS: 行级点对点隐私 —— 仅收发双方可见, 与群组隔离正交
-- - 检索索引: 收件箱复合索引 + pg_trgm 模糊检索索引
-- 幂等执行。pg_trgm 在迁移内自建, 不依赖运行期 ensure 状态, 保证迁移链自给自足。
-- ============================================================================

-- 0. 检索依赖扩展 (幂等; 迁移以超级用户/建库角色执行)
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- 1. 归档列
ALTER TABLE shared.agent_messages ADD COLUMN IF NOT EXISTS is_archived BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE shared.agent_messages ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;

-- 2. 收件人信箱余量函数 (SECURITY DEFINER: 发件人视角校验收件人余量, 无需直读全表)
CREATE OR REPLACE FUNCTION shared.acl_mailbox_has_room(p_recipient text) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = shared, pg_temp AS $$
    SELECT COUNT(*) < COALESCE(NULLIF(current_setting('logbook.mailbox_cap', true), '')::int, 500)
    FROM shared.agent_messages
    WHERE to_agent = p_recipient AND is_read = FALSE AND is_archived = FALSE;
$$;

-- 3. 信箱行级安全: 点对点隐私 (仅收发双方; 表 owner 系统面天然豁免供审计)
ALTER TABLE shared.agent_messages ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_msg_select ON shared.agent_messages;
DROP POLICY IF EXISTS p_msg_insert ON shared.agent_messages;
DROP POLICY IF EXISTS p_msg_update ON shared.agent_messages;
CREATE POLICY p_msg_select ON shared.agent_messages
    FOR SELECT
    USING (
        from_agent = shared.acl_current_agent()
        OR to_agent = shared.acl_current_agent()
    );
CREATE POLICY p_msg_insert ON shared.agent_messages
    FOR INSERT
    WITH CHECK (from_agent = shared.acl_current_agent());
CREATE POLICY p_msg_update ON shared.agent_messages
    FOR UPDATE
    USING (from_agent = shared.acl_current_agent() OR to_agent = shared.acl_current_agent())
    WITH CHECK (from_agent = shared.acl_current_agent() OR to_agent = shared.acl_current_agent());

-- 4. 索引: 收件箱过滤 + 归档 + trgm 模糊检索
CREATE INDEX IF NOT EXISTS idx_agent_messages_recipient_box
    ON shared.agent_messages (to_agent, is_archived, is_read, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_messages_sender_box
    ON shared.agent_messages (from_agent, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agent_messages_trgm_subject
    ON shared.agent_messages USING gin (subject gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_agent_messages_trgm_content
    ON shared.agent_messages USING gin (content gin_trgm_ops);
