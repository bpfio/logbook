-- ============================================================================
-- Logbook 0.4.1: 收件人鉴权读取审计
-- 非收件人不能读取信件；收件人读取时记录实际核销身份。
-- ============================================================================

ALTER TABLE shared.agent_messages
    ADD COLUMN IF NOT EXISTS read_by_agent VARCHAR(64);

CREATE INDEX IF NOT EXISTS idx_agent_messages_read_by
    ON shared.agent_messages (to_agent, read_by_agent, read_at);
