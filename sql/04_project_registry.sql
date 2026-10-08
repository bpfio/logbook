-- ============================================================================
-- Logbook Git 仓库权威注册中心与多项目双轨索引迁移脚本 (04_project_registry.sql)
-- 幂等执行: 支持多轮执行不破坏存量数据
-- ============================================================================

-- 1. 创建共享项目注册表 (shared.projects)
CREATE TABLE IF NOT EXISTS shared.projects (
    slug VARCHAR(64) PRIMARY KEY,            -- 权威 Git 坐标 (如 'bpfio/logbook', 'bpfio/brix', 'io/TS')
    schema_name VARCHAR(32) NOT NULL UNIQUE, -- 物理 DB Schema (如 'logbook', 'brix', 'ts')
    title VARCHAR(256) NOT NULL,             -- 项目全称 / 中文名称
    description TEXT,                        -- 项目描述与定位
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_projects_schema ON shared.projects(schema_name);

-- 2. 幂等补偿注册存量核心项目
INSERT INTO shared.projects (slug, schema_name, title, description)
VALUES 
    ('bpfio/logbook', 'logbook', 'Logbook 工业级研发台账与排查手记中枢', 'Agent-Native 研发台账、根因手记、架构铁律与长期记忆底座'),
    ('bpfio/brix', 'brix', 'Brix 软路由核心系统', '基于 eBPF 与 XDP 的高性能软路由数据面与控制面')
ON CONFLICT (slug) DO UPDATE 
SET schema_name = EXCLUDED.schema_name,
    title = EXCLUDED.title,
    description = COALESCE(EXCLUDED.description, shared.projects.description),
    updated_at = CURRENT_TIMESTAMP;

-- 3. 项目注册存储过程封装 (供 Agent MCP project_init 与运维脚本调用)
CREATE OR REPLACE FUNCTION shared.register_project(
    p_slug TEXT,
    p_schema TEXT,
    p_title TEXT,
    p_desc TEXT DEFAULT NULL
)
RETURNS VOID AS $$
BEGIN
    -- 确保基础 Schema 与台账表已初始化
    PERFORM shared.init_project_schema(p_schema);

    -- 登记至 shared.projects 元数据中心
    INSERT INTO shared.projects (slug, schema_name, title, description)
    VALUES (p_slug, p_schema, p_title, p_desc)
    ON CONFLICT (slug) DO UPDATE
    SET schema_name = EXCLUDED.schema_name,
        title = EXCLUDED.title,
        description = COALESCE(EXCLUDED.description, shared.projects.description),
        updated_at = CURRENT_TIMESTAMP;
END;
$$ LANGUAGE plpgsql;
