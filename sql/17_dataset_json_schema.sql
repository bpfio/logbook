-- ==============================================================================
-- 迁移 17: shared.datasets 增加 json_schema 字段与核心数据集契约预置
-- 作用: 强化软肋骨字段物理防御，声明式校验 payload 关键结构
-- ==============================================================================

ALTER TABLE shared.datasets ADD COLUMN IF NOT EXISTS json_schema jsonb DEFAULT NULL;

-- 1. sys_task 契约预置 (要求 task_id 与 title 必填且非空)
UPDATE shared.datasets 
SET json_schema = '{
  "type": "object",
  "required": ["task_id", "title"],
  "properties": {
    "task_id": {"type": "string", "minLength": 1},
    "title": {"type": "string", "minLength": 1},
    "status": {"type": "string"},
    "priority": {"type": "string"},
    "task_type": {"type": "string"}
  }
}'::jsonb
WHERE code = 'sys_task';

-- 2. sys_devlog 契约预置 (要求四要素必填: title, problem, root_cause, solution, evidence)
UPDATE shared.datasets 
SET json_schema = '{
  "type": "object",
  "required": ["title", "problem", "root_cause", "solution", "evidence"],
  "properties": {
    "title": {"type": "string", "minLength": 1},
    "problem": {"type": "string", "minLength": 1},
    "root_cause": {"type": "string", "minLength": 1},
    "solution": {"type": "string", "minLength": 1},
    "evidence": {"type": "string", "minLength": 1}
  }
}'::jsonb
WHERE code = 'sys_devlog';

-- 3. sys_finding 契约预置 (要求 summary 必填)
UPDATE shared.datasets 
SET json_schema = '{
  "type": "object",
  "required": ["summary"],
  "properties": {
    "finding_id": {"type": "string"},
    "summary": {"type": "string", "minLength": 1},
    "severity": {"type": "string"},
    "status": {"type": "string"}
  }
}'::jsonb
WHERE code = 'sys_finding';

-- 4. sys_waiting 契约预置 (要求 description 必填)
UPDATE shared.datasets 
SET json_schema = '{
  "type": "object",
  "required": ["description"],
  "properties": {
    "waiting_id": {"type": "string"},
    "description": {"type": "string", "minLength": 1},
    "category": {"type": "string"}
  }
}'::jsonb
WHERE code = 'sys_waiting';

-- 5. sys_batch 契约预置 (要求 title 必填)
UPDATE shared.datasets 
SET json_schema = '{
  "type": "object",
  "required": ["title"],
  "properties": {
    "batch_id": {"type": "string"},
    "title": {"type": "string", "minLength": 1}
  }
}'::jsonb
WHERE code = 'sys_batch';

-- 6. sys_rule 契约预置 (要求 title 与 summary 必填)
UPDATE shared.datasets 
SET json_schema = '{
  "type": "object",
  "required": ["title", "summary"],
  "properties": {
    "rule_id": {"type": "string"},
    "title": {"type": "string", "minLength": 1},
    "summary": {"type": "string", "minLength": 1}
  }
}'::jsonb
WHERE code = 'sys_rule';

-- 7. sys_research 契约预置 (要求 title, objective, decision 必填)
UPDATE shared.datasets 
SET json_schema = '{
  "type": "object",
  "required": ["title", "objective", "decision"],
  "properties": {
    "title": {"type": "string", "minLength": 1},
    "objective": {"type": "string", "minLength": 1},
    "decision": {"type": "string", "minLength": 1}
  }
}'::jsonb
WHERE code = 'sys_research';

-- 8. sys_message 契约预置 (要求 from_agent, to_agent, subject, content 必填)
UPDATE shared.datasets 
SET json_schema = '{
  "type": "object",
  "required": ["from_agent", "to_agent", "subject", "content"],
  "properties": {
    "from_agent": {"type": "string", "minLength": 1},
    "to_agent": {"type": "string", "minLength": 1},
    "subject": {"type": "string", "minLength": 1},
    "content": {"type": "string", "minLength": 1}
  }
}'::jsonb
WHERE code = 'sys_message';
