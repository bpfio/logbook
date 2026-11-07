-- ==============================================================================
-- Logbook 增量迁移 18: 增强 sys_research 数据集 JSON Schema (补齐 tradeoffs 必填项)
-- 适用范围: 生产环境与全新部署
-- ==============================================================================

UPDATE shared.datasets 
SET json_schema = '{
  "type": "object",
  "required": ["title", "objective", "tradeoffs", "decision"],
  "properties": {
    "title": {"type": "string", "minLength": 1},
    "objective": {"type": "string", "minLength": 1},
    "tradeoffs": {"type": "string", "minLength": 1},
    "decision": {"type": "string", "minLength": 1}
  }
}'::jsonb
WHERE code = 'sys_research';
