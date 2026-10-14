"""多用户分组权限 + 站内邮箱完成面集成测试 (0.5.0, 批次 DEV-2026-10-14-01)。

语义 (方案 A): 组内互见互写, 跨组不可见; 未挂组项目 legacy 全通; admin 全见。
信箱: 点对点隐私 (RLS 仅收发双方), 收件人余量上限, 归档/会话/已发送/搜索。

依赖本地 PG (DATABASE_URL 指向临时测试库), 与 test_db_layer.py 同环境。
所有写入均使用 mu_probe_* / mu_* 账户隔离; 状态在模块 setup 前重置。
注意: 信件检索断言一律使用 ASCII 标记 (PG simple 配置不分词 CJK)。
"""

import os

import pytest
import asyncpg

os.environ.setdefault("LOGBOOK_ADMIN_AGENTS", "agy")

from logbook.db import db
from logbook.errors import ToolError
from logbook.models import DevLog, DevLogVisibility, Task, TaskStatus

pytestmark = pytest.mark.integration

P_OPEN = "mu_probe_open"      # legacy 开放态项目 (不挂组)
P_CLOSED = "mu_probe_closed"  # 挂组收口项目
G_DEV = "mu_devs"
ALICE, BOB, CAROL, ADMIN = "mu_alice", "mu_bob", "mu_carol", "agy"
GHOST = "mu_ghost_user"

_prepared = False


async def _become(identity: str | None):
    """切换数据面会话身份 (重建连接池使 SET ROLE 生效)。"""
    db.identity = identity
    db._identity_active = identity is not None
    await db.close()


async def _hard_reset():
    """系统面清理上一轮遗留状态 (幂等, 只清 mu_ 探针面)。"""
    await _become(None)
    spool = await db._acquire_system()
    async with spool.acquire() as conn:
        for sch in (P_OPEN, P_CLOSED):
            await conn.execute(f'DROP SCHEMA IF EXISTS "{sch}" CASCADE')
        await conn.execute("DELETE FROM shared.project_groups WHERE schema_name IN ($1, $2)", P_OPEN, P_CLOSED)
        await conn.execute("DELETE FROM shared.projects WHERE schema_name IN ($1, $2)", P_OPEN, P_CLOSED)
        await conn.execute(
            "DELETE FROM shared.agent_messages WHERE from_agent LIKE 'mu_%' OR to_agent LIKE 'mu_%'"
        )
        await conn.execute("DELETE FROM shared.devlogs WHERE project_id IN ($1, $2)", P_OPEN, P_CLOSED)
        await conn.execute("DELETE FROM shared.groups WHERE group_name LIKE 'mu_%'")
        await conn.execute("DELETE FROM shared.principals WHERE agent_name LIKE 'mu_%'")
        dbname = await conn.fetchval("SELECT current_database()")
        await conn.execute(f'ALTER DATABASE "{dbname}" RESET logbook.mailbox_cap')


async def _setup_fixtures():
    """系统面引导: admin/账户/群组/两类项目 (幂等)。"""
    global _prepared
    if _prepared:
        return
    await _hard_reset()
    await db.ensure_acl_bootstrap()
    await db.ensure_principal(ADMIN, is_admin=True)
    for name in (ALICE, BOB, CAROL):
        await db.ensure_principal(name)

    await db.create_group(G_DEV, "mu 测试群组", ADMIN)
    await db.add_group_member(G_DEV, ALICE, ADMIN)

    await db.register_project(slug=P_OPEN, title="MU 开放探针")
    await db.register_project(slug=P_CLOSED, title="MU 收口探针")
    await db.grant_group_project(G_DEV, P_CLOSED, ADMIN)
    await db.reset_data_pool()
    _prepared = True


@pytest.fixture(autouse=True)
async def _module_state():
    await _setup_fixtures()
    yield
    # 每用例收尾归位 owner 面, 不向后续测试泄漏降权会话身份
    await _become(None)


# =============================================================================
# 批次 A: 分组权限
# =============================================================================

@pytest.mark.asyncio
async def test_01_principal_provisioning_and_roles():
    """账户注册: principals 行 + PG 账户角色 + admin 标记 + 群组成员关系。"""
    assert await db.is_admin(ADMIN) is True
    assert await db.is_admin(ALICE) is False
    assert await db.principal_exists(ALICE) is True
    assert await db.principal_exists(GHOST) is False

    spool = await db._acquire_system()
    async with spool.acquire() as conn:
        assert await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'lb_u_mu_alice')"
        )
        assert await conn.fetchval(
            "SELECT pg_has_role('lb_u_mu_alice', 'lb_g_mu_devs', 'member')"
        ) is True
        assert await conn.fetchval(
            "SELECT pg_has_role('lb_u_mu_bob', 'lb_g_mu_devs', 'member')"
        ) is False


@pytest.mark.asyncio
async def test_02_legacy_open_project_visible_to_all():
    """未挂组项目 legacy 全通: 任意账户均可读写。"""
    await _become(BOB)
    proj = await db.get_project(P_OPEN)
    assert proj is not None
    task = await db.upsert_task(
        P_OPEN, Task(id="MU-T-OPEN", title="开放项目写入", status=TaskStatus.PLANNED, assignee=BOB)
    )
    assert task.id == "MU-T-OPEN"
    await _become(CAROL)
    tasks = await db.query_tasks(P_OPEN)
    assert any(t.id == "MU-T-OPEN" for t in tasks)


@pytest.mark.asyncio
async def test_03_attached_project_group_isolation():
    """挂组收口: 组内互见互写; 跨组不可见 (查询为空 + 写被 PG 硬拒)。"""
    await _become(ALICE)
    proj = await db.get_project(P_CLOSED)
    assert proj is not None
    await db.upsert_task(
        P_CLOSED, Task(id="MU-T-A1", title="组内写入", status=TaskStatus.PLANNED, assignee=ALICE)
    )

    await _become(BOB)
    assert await db.get_project(P_CLOSED) is None
    assert all(p.schema_name != P_CLOSED for p in await db.list_projects())
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await db.upsert_task(
            P_CLOSED, Task(id="MU-T-B1", title="越权写入", status=TaskStatus.PLANNED, assignee=BOB)
        )

    await _become(CAROL)
    assert await db.get_project(P_CLOSED) is None


@pytest.mark.asyncio
async def test_04_admin_sees_everything():
    """admin 全见: 挂组项目对 admin 可见; probe 可代查任意身份视角。"""
    await _become(ADMIN)
    assert await db.get_project(P_CLOSED) is not None
    assert await db.probe_project(P_CLOSED, agent=ADMIN) == {"exists": True, "visible": True}
    assert await db.probe_project(P_CLOSED, agent=BOB) == {"exists": True, "visible": False}


@pytest.mark.asyncio
async def test_05_grant_then_reveal_revoke_then_hide():
    """加组即显现可写, 移出即隐没 (SET ROLE 快照经池重建刷新)。"""
    await db.add_group_member(G_DEV, BOB, ADMIN)
    await db.reset_data_pool()
    await _become(BOB)
    assert await db.get_project(P_CLOSED) is not None
    await db.upsert_task(
        P_CLOSED, Task(id="MU-T-B2", title="加组后写入", status=TaskStatus.PLANNED, assignee=BOB)
    )

    assert await db.remove_group_member(G_DEV, BOB, ADMIN) is True
    await db.reset_data_pool()
    await _become(BOB)
    assert await db.get_project(P_CLOSED) is None
    with pytest.raises(asyncpg.InsufficientPrivilegeError):
        await db.upsert_task(
            P_CLOSED, Task(id="MU-T-B3", title="移组后写入", status=TaskStatus.PLANNED, assignee=BOB)
        )


@pytest.mark.asyncio
async def test_06_revoke_project_returns_legacy_open():
    """摘除唯一挂载组 → 项目回到 legacy 开放态; 重新挂载 → 再收口。"""
    await db.revoke_group_project(G_DEV, P_CLOSED, ADMIN)
    await db.reset_data_pool()
    await _become(BOB)
    assert await db.get_project(P_CLOSED) is not None
    await db.grant_group_project(G_DEV, P_CLOSED, ADMIN)
    await db.reset_data_pool()
    await _become(BOB)
    assert await db.get_project(P_CLOSED) is None


@pytest.mark.asyncio
async def test_07_devlog_group_visibility_and_public_safe():
    """手记知识库: 跨组不可见; public_safe 密级保持全局可见 (ASCII 标记规避 CJK 分词)。"""
    await _become(ALICE)
    await db.record_devlog(DevLog(
        project_id=P_CLOSED, task_id="MU-T-A1", title="mu-group-note",
        problem="p", root_cause="r", solution="s", evidence="e", author=ALICE,
    ))
    await db.record_devlog(DevLog(
        project_id=P_CLOSED, task_id=None, title="mu-open-note",
        problem="p", root_cause="r", solution="s", evidence="e", author=ALICE,
        visibility=DevLogVisibility.PUBLIC_SAFE,
    ))

    await _become(BOB)
    found = await db.search_devlogs(P_CLOSED, query_text="note")
    titles = {d["title"] for d in found}
    assert "mu-open-note" in titles
    assert "mu-group-note" not in titles

    await _become(ALICE)
    mine = await db.search_devlogs(P_CLOSED, query_text="note")
    assert {"mu-group-note", "mu-open-note"} <= {d["title"] for d in mine}


@pytest.mark.asyncio
async def test_08_mcp_group_tools_admin_gate(monkeypatch):
    """管理面门禁: 非 admin 调 group_record 拒绝; admin 可创建; principal_query 返回画像。"""
    from logbook import mcp_server

    monkeypatch.setenv("LOGBOOK_AGENT_ID", ALICE)
    denied = await mcp_server.group_record(group_name="mu_rogue")
    assert denied.get("isError") is True and denied.get("error_type") == "ADMIN_REQUIRED"

    monkeypatch.setenv("LOGBOOK_AGENT_ID", ADMIN)
    ok = await mcp_server.group_record(group_name="mu_rogue", description="临时")
    assert ok.get("ok") is True
    assert await db.is_admin(ADMIN) is True

    monkeypatch.setenv("LOGBOOK_AGENT_ID", ALICE)
    profile = await mcp_server.principal_query()
    assert profile["is_admin"] is False and G_DEV in profile["groups"]

    groups = await mcp_server.group_query()
    names = [g["group_name"] for g in groups["groups"]]
    assert G_DEV in names


@pytest.mark.asyncio
async def test_09_mcp_project_init_visibility_guard(monkeypatch):
    """project_init 语义化拒绝: 对不可见的挂组项目 init 返回 PROJECT_NOT_VISIBLE 而非误建。"""
    from logbook import mcp_server

    monkeypatch.setenv("LOGBOOK_AGENT_ID", BOB)
    monkeypatch.setenv("LOGBOOK_PROJECT", P_CLOSED)
    res = await mcp_server.project_init(repo=P_CLOSED, title="越权重建")
    assert res.get("isError") is True and res.get("error_type") == "PROJECT_NOT_VISIBLE"


# =============================================================================
# 批次 B: 站内邮箱
# =============================================================================

@pytest.mark.asyncio
async def test_10_mailbox_point_to_point_privacy():
    """信箱 RLS: 收发双方可见; 第三方 (含同组) 不可见不可读不可销。"""
    await _become(ALICE)
    msg = await db.send_agent_message(
        project_id=P_CLOSED, from_agent=ALICE, to_agent=BOB,
        subject="MU secret", content="mu-secret-2026 marker",
    )
    assert msg.id > 0

    await _become(BOB)
    assert any(m.id == msg.id for m in await db.get_agent_inbox(None, BOB))

    await _become(CAROL)
    assert all(m.id != msg.id for m in await db.get_agent_inbox(None, CAROL))
    assert all(m.id != msg.id for m in await db.search_agent_messages(CAROL, "mu-secret-2026"))
    assert await db.read_agent_message(msg.id, CAROL) is None

    await _become(BOB)
    read = await db.read_agent_message(msg.id, BOB)
    assert read is not None and read.is_read is True and read.read_by_agent == BOB

    await _become(ALICE)
    assert any(m.id == msg.id for m in await db.get_agent_sent(ALICE))


@pytest.mark.asyncio
async def test_11_reply_thread_sent_search():
    """回复/会话/搜索闭环: 线程沿用、对方自动推导、第三方不可见。"""
    await _become(ALICE)
    root = await db.send_agent_message(
        project_id=P_OPEN, from_agent=ALICE, to_agent=BOB,
        subject="MU thread", content="thread-origin-mark",
    )
    await _become(BOB)
    reply = await db.reply_agent_message(root.id, BOB, "ack done")
    assert reply is not None
    assert reply.to_agent == ALICE
    assert reply.thread_id == f"t-{root.id}"
    assert reply.subject.startswith("Re:")

    await _become(ALICE)
    thread_alice = await db.get_agent_thread(reply.thread_id, ALICE)
    assert {root.id, reply.id} <= {m.id for m in thread_alice}
    await _become(CAROL)
    assert await db.get_agent_thread(reply.thread_id, CAROL) == []

    await _become(BOB)
    hits = await db.search_agent_messages(BOB, "thread-origin-mark")
    assert any(m.id == root.id for m in hits)


@pytest.mark.asyncio
async def test_12_archive_keeps_out_of_inbox():
    """归档: 移出未读收件箱; 仅收件人可操作。"""
    await _become(CAROL)
    m = await db.send_agent_message(
        project_id=P_OPEN, from_agent=CAROL, to_agent=BOB,
        subject="MU archive probe", content="archive-me",
    )
    await _become(BOB)
    assert any(x.id == m.id for x in await db.get_agent_inbox(None, BOB))
    archived = await db.archive_agent_message(m.id, BOB, True)
    assert archived is not None and archived.is_archived is True
    assert all(x.id != m.id for x in await db.get_agent_inbox(None, BOB))
    restored = await db.archive_agent_message(m.id, BOB, False)
    assert restored.is_archived is False
    assert any(x.id == m.id for x in await db.get_agent_inbox(None, BOB))
    await _become(CAROL)
    assert await db.archive_agent_message(m.id, CAROL, True) is None


@pytest.mark.asyncio
async def test_13_mailbox_capacity_rejects_delivery():
    """容量上限: GUC logbook.mailbox_cap 调低后超额投递被拒; 读销一封后余量恢复。"""
    spool = await db._acquire_system()
    async with spool.acquire() as conn:
        dbname = await conn.fetchval("SELECT current_database()")
        await conn.execute(f'ALTER DATABASE "{dbname}" SET logbook.mailbox_cap = \'2\'')
        # 清空 bob 未读, 保证容量计数从零起算
        await conn.execute("UPDATE shared.agent_messages SET is_read = TRUE WHERE to_agent = $1", BOB)
    await db.reset_data_pool()
    spool = await db._acquire_system()

    await _become(CAROL)
    ids = []
    for i in range(2):
        msg = await db.send_agent_message(
            project_id=P_OPEN, from_agent=CAROL, to_agent=BOB,
            subject=f"MU cap {i}", content="cap",
        )
        ids.append(msg.id)
    with pytest.raises(ToolError) as ei:
        await db.send_agent_message(
            project_id=P_OPEN, from_agent=CAROL, to_agent=BOB,
            subject="MU cap 3", content="cap",
        )
    assert ei.value.error_type == "RECIPIENT_MAILBOX_FULL"

    await _become(BOB)
    await db.read_agent_message(ids[0], BOB)
    await _become(CAROL)
    ok = await db.send_agent_message(
        project_id=P_OPEN, from_agent=CAROL, to_agent=BOB,
        subject="MU cap 4", content="cap",
    )
    assert ok.id > ids[-1]

    await db.reset_data_pool()
    spool = await db._acquire_system()
    async with spool.acquire() as conn:
        await conn.execute(f'ALTER DATABASE "{dbname}" RESET logbook.mailbox_cap')
    await db.reset_data_pool()


@pytest.mark.asyncio
async def test_14_unregistered_recipient_warning_via_mcp(monkeypatch):
    """未注册收件人: 投递成功但带 recipient_registered=False 告警。"""
    from logbook import mcp_server

    monkeypatch.setenv("LOGBOOK_AGENT_ID", ALICE)
    monkeypatch.setenv("LOGBOOK_PROJECT", P_OPEN)
    await _become(ALICE)  # 会话身份与 env 对齐 (生产由 run_stdio 引导完成)
    res = await mcp_server.message_send(
        project=P_OPEN, from_agent=ALICE, to_agent=GHOST,
        subject="ghost recipient", content="who are you",
    )
    assert res.get("ok") is True
    assert res.get("recipient_registered") is False
    assert "warning" in res
