"""Logbook 项目注册中心与双轨寻址 (Dual-Track Resolution) 单元测试套件

测试项:
1. slug 与物理 schema 转换映射规则 (slug_to_schema_name)
2. Project 模型与标识符规范校验
3. 数据库层 register_project / get_project 双轨解析 / list_projects
4. 智能协商双轨解析与项目自愈指引 (PROJECT_INIT_NEEDED)
5. MCP project_init 工具端到端自开立与幂等性
"""

import pytest
from logbook.models import Project, slug_to_schema_name
from logbook.negotiation import (
    validate_and_negotiate_project,
    ProjectNegotiationError,
    detect_current_workspace_project
)
from logbook.mcp_server import project_init, get_projects_resource
from logbook.db import db


def test_slug_to_schema_name():
    """测试将权威 Git 坐标转换为合规物理 Schema 名称。"""
    assert slug_to_schema_name("bpfio/brix") == "brix"
    assert slug_to_schema_name("bpfio/logbook") == "logbook"
    assert slug_to_schema_name("io/TS") == "ts"
    assert slug_to_schema_name("my-new-service") == "my_new_service"
    assert slug_to_schema_name("TAX") == "tax"

    with pytest.raises(ValueError):
        slug_to_schema_name("")

    with pytest.raises(ValueError):
        slug_to_schema_name("///")

    with pytest.raises(ValueError):
        slug_to_schema_name("---")

    # 数字开头自动补全 p_ 前缀以符合 PG 标识符规范
    assert slug_to_schema_name("123_valid_start") == "p_123_valid_start"


def test_project_model_validation():
    """测试 Project 模型对合法与非法 slug 的校验。"""
    p = Project(slug="bpfio/brix", schema_name="brix", title="Brix 核心系统")
    assert p.slug == "bpfio/brix"
    assert p.schema_name == "brix"

    # 非法格式应抛出校验错误
    with pytest.raises(Exception):
        Project(slug="/brix", schema_name="brix", title="非法前导斜杠")

    with pytest.raises(Exception):
        Project(slug="bpfio/", schema_name="brix", title="非法尾部斜杠")

    with pytest.raises(Exception):
        Project(slug="bpfio/brix/extra", schema_name="brix", title="非法多级路径")


@pytest.mark.asyncio
async def test_db_project_registry_and_dual_track():
    """测试数据库层 register_project 登记与双轨寻址解析。"""
    repo_slug = "bpfio/dual_test_probe"
    schema = slug_to_schema_name(repo_slug)

    # 1. 注册新项目
    proj = await db.register_project(
        slug=repo_slug,
        title="双轨寻址探针",
        description="用于单元测试的项目"
    )
    assert proj.slug == repo_slug
    assert proj.schema_name == schema

    # 2. 权威坐标寻址 (Slug Track)
    p_by_slug = await db.get_project(repo_slug)
    assert p_by_slug is not None
    assert p_by_slug.schema_name == schema

    # 3. 物理短名寻址 (Schema Track)
    p_by_schema = await db.get_project(schema)
    assert p_by_schema is not None
    assert p_by_schema.slug == repo_slug

    # 4. 幂等更新
    proj_updated = await db.register_project(
        slug=repo_slug,
        title="双轨寻址探针-更新后",
        description="更新说明"
    )
    assert proj_updated.title == "双轨寻址探针-更新后"

    # 5. 清理测试资源
    async with db._pool.acquire() as conn:
        await conn.execute("DELETE FROM shared.projects WHERE slug = $1", repo_slug)
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


@pytest.mark.asyncio
async def test_negotiation_dual_track_and_init_guide():
    """测试智能协商的双轨解析支持以及未初始化项目时的自愈指引。"""
    curr_ws = detect_current_workspace_project()
    # 当前工作区在 logbook 仓库中，测试双轨匹配均放行当前工作区
    if curr_ws in ("logbook", "bpfio/logbook"):
        schema1 = await validate_and_negotiate_project("logbook")
        assert schema1 == "logbook"

        schema2 = await validate_and_negotiate_project("bpfio/logbook")
        assert schema2 == "logbook"

    # 跨项目或不存在项目（允许跨项目协商）
    with pytest.raises(ProjectNegotiationError) as exc_info:
        await validate_and_negotiate_project("unknown_org/completely_new_repo", allow_cross_project=True)

    err = exc_info.value
    assert err.action_required == "PROJECT_INIT_NEEDED"
    assert "project_init" in err.message


@pytest.mark.asyncio
async def test_mcp_project_init_and_resource():
    """测试 MCP 工具 project_init 端到端初始化与 logbook://projects 资源直读。"""
    test_slug = "bpfio/mcp_init_probe"
    schema = slug_to_schema_name(test_slug)

    # 1. 注册必填参数校验
    err_resp = await project_init(repo="", title="")
    assert err_resp["isError"] is True

    # 2. 正确自开立
    res = await project_init(
        repo=test_slug,
        title="MCP开立测试项目",
        description="自动化测试自开立"
    )
    assert res["success"] is True
    assert res["ok"] is True
    assert res["repo"] == test_slug
    assert res["project"] == schema

    # 3. 验证 logbook://projects 资源包含该项目
    projects_md = await get_projects_resource()
    assert test_slug in projects_md
    assert "MCP开立测试项目" in projects_md

    # 4. 清理
    async with db._pool.acquire() as conn:
        await conn.execute("DELETE FROM shared.projects WHERE slug = $1", test_slug)
        await conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
