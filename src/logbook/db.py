"""Logbook 异步数据库操作层 (asyncpg + PostgreSQL 18)

特性:
- 二进制协议直连，零外部 C 库依赖
- 连接池严格硬锁 (min_size=1, max_size=3, 符合 CPU*2 黄金法则)
- 双平面 Schema 隔离自动路由
- 向量 HNSW 距离与全文混合召回
"""

import asyncio
import hashlib
import logging
import os
import json
import re
from datetime import datetime, date
from decimal import Decimal
from typing import Any
import asyncpg
from .models import (
    Project, slug_to_schema_name,
    AgentMessage, FileLease,
    Dataset, DataRecord, DatasetCategory, RecordStatus
)
from .time_sync import get_beijing_now

logger = logging.getLogger(__name__)


# LIMIT 统一钳制上限 (P1-4: 防 f-string 注入面 + 防 token 肥胖)
MAX_LIMIT = 200


class Database:
    @staticmethod
    def _clamp_limit(limit: int | None, default: int = 50) -> int:
        """钳制 LIMIT 到 [1, MAX_LIMIT]，非法值回落 default。"""
        try:
            n = int(limit)
        except (TypeError, ValueError):
            return default
        if n < 1:
            return default
        return min(n, MAX_LIMIT)

    def __init__(self, database_url: str | None = None):
        db_url = database_url or os.getenv("DATABASE_URL")
        if db_url:
            from urllib.parse import urlparse
            u = urlparse(db_url)
            self.host = u.hostname or "127.0.0.1"
            self.port = u.port or 5432
            self.user = u.username or "postgres"
            self.password = u.password or ""
            self.database = u.path.lstrip("/") or "logbook"
        else:
            self.host = os.getenv("LOGBOOK_PG_HOST", "127.0.0.1")
            self.port = int(os.getenv("LOGBOOK_PG_PORT", "5432"))
            self.user = os.getenv("LOGBOOK_PG_USER", os.getenv("POSTGRES_USER", "logbook"))
            self.password = os.getenv("LOGBOOK_PG_PASSWORD", os.getenv("POSTGRES_PASSWORD", "logbook_dev_secret"))
            self.database = os.getenv("LOGBOOK_PG_DB", os.getenv("POSTGRES_DB", "logbook"))
        # 多用户双平面: identity 来自可信环境变量 (SSH 会话/stdio 注入)。
        # 数据面池 (self._pool) init 钩子执行 SET ROLE 降权到账户角色,
        # 系统面池 (self._system_pool) 以登录角色执行 DDL/授权等特权操作。
        # identity 为空 (CLI/迁移/存量测试) 时数据面不降权, 行为与单用户时代完全一致。
        self.identity: str | None = os.getenv("LOGBOOK_AGENT_ID", "").strip() or None
        self._pool: asyncpg.Pool | None = None
        self._system_pool: asyncpg.Pool | None = None
        self._runtime_loop: asyncio.AbstractEventLoop | None = None
        self._async_locks: dict[str, asyncio.Lock] = {}
        self._messaging_schema_ensured: bool = False
        self._datasets_and_records_schema_ensured: bool = False
        self._project_ready: set[str] = set()
        self._acl_bootstrapped: bool = False
        self._identity_active: bool = False

    def _get_lock(self, name: str) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._runtime_loop is not loop:
            self._runtime_loop = loop
            self._async_locks.clear()
            self._project_ready.clear()
            self._messaging_schema_ensured = False
            self._datasets_and_records_schema_ensured = False
        lock = self._async_locks.get(name)
        if lock is None:
            lock = asyncio.Lock()
            self._async_locks[name] = lock
        return lock

    # =========================================================================
    # 多用户 ACL: 角色/池基座 (0.5.0)
    # =========================================================================

    @staticmethod
    def _qi(ident: str) -> str:
        """PG 标识符安全引用 (角色名/schema 名均已由上游正则约束)。"""
        return '"' + ident.replace('"', '""') + '"'

    @staticmethod
    def pg_role_name(kind: str, name: str) -> str:
        """确定性 PG 角色名: lb_u_<principal> / lb_g_<group>。

        PG 标识符硬上限 63 字节, 超长时以内容哈希截断保持确定性。
        """
        base = f"lb_{kind}_{name}"
        if len(base.encode()) <= 63:
            return base
        return f"lb_{kind}_{hashlib.sha256(name.encode()).hexdigest()[:20]}"

    @property
    def data_role_name(self) -> str | None:
        return self.pg_role_name("u", self.identity) if self.identity else None

    @property
    def acl_bootstrapped(self) -> bool:
        """ACL 基座是否已引导成功 (决定 ensure_project 是否补开放授权)。"""
        return self._acl_bootstrapped

    async def connect(self, plane: str = "data"):
        loop = asyncio.get_running_loop()
        async with self._get_lock(f"connect:{plane}"):
            if plane == "data":
                pool_attr, init_hook = "_pool", self._init_data_conn
            else:
                pool_attr, init_hook = "_system_pool", None
            pool = getattr(self, pool_attr)
            if pool is not None and getattr(pool, "_loop", None) != loop:
                setattr(self, pool_attr, None)
                try:
                    pool.terminate()
                except Exception:
                    pass
                pool = None

            if pool is None:
                cmd_timeout = float(os.getenv("LOGBOOK_COMMAND_TIMEOUT", "20.0"))
                pool = await asyncpg.create_pool(
                    host=self.host,
                    port=self.port,
                    user=self.user,
                    password=self.password,
                    database=self.database,
                    min_size=1,
                    max_size=3,
                    command_timeout=cmd_timeout,
                    init=init_hook,
                    reset=self._reset_data_conn if plane == "data" else None,
                )
                setattr(self, pool_attr, pool)

    async def _init_data_conn(self, conn: asyncpg.Connection):
        """数据面连接降权: 会话身份绑定 (SET ROLE + logbook.agent GUC)。

        仅在身份引导 (ensure_principal) 成功后生效; 失败降级 legacy 不降权。
        """
        if self._identity_active and self.data_role_name:
            await conn.execute("SELECT set_config('role', $1, false)", self.data_role_name)
            await conn.execute("SELECT set_config('logbook.agent', $1, false)", self.identity)
        elif self.identity:
            # 有身份但未引导成功 (如无 CREATEROLE 权限): 保持 legacy 全通并记录
            await conn.execute("SELECT set_config('logbook.agent', $1, false)", self.identity)

    async def _reset_data_conn(self, conn: asyncpg.Connection):
        """数据面连接归还钩子: 替换 asyncpg 默认 reset。

        默认 reset 会执行 RESET ALL, 把 init 钩子设置的 logbook.agent 会话
        变量清空 (角色保留但身份 GUC 丢失, RLS 语义被破坏); 此处改为归还时
        重新断言会话身份, 同时重置会话级 GUC 参数 (如 hnsw.ef_search, OPT-17)。
        """
        if self._identity_active and self.data_role_name:
            await conn.execute("SELECT set_config('role', $1, false)", self.data_role_name)
            await conn.execute("SELECT set_config('logbook.agent', $1, false)", self.identity)
        try:
            await conn.execute("RESET hnsw.ef_search;")
        except Exception:
            pass

    async def _acquire_system(self) -> asyncpg.pool.Pool:
        """系统面连接池 (登录角色, 用于 DDL/授权/引导等特权操作)。"""
        await self.connect(plane="system")
        return self._system_pool

    def _data_plane_ready(self) -> bool:
        return self._pool is not None

    async def close(self):
        loop = asyncio.get_running_loop()
        async with self._get_lock("connect:data"):
            pool = self._pool
            self._pool = None
            self._project_ready.clear()
            self._messaging_schema_ensured = False
            self._datasets_and_records_schema_ensured = False
            if pool is not None:
                if getattr(pool, "_loop", None) != loop:
                    try:
                        pool.terminate()
                    except Exception:
                        pass
                else:
                    await pool.close()
        async with self._get_lock("connect:system"):
            spool = self._system_pool
            self._system_pool = None
            if spool is not None:
                if getattr(spool, "_loop", None) != loop:
                    try:
                        spool.terminate()
                    except Exception:
                        pass
                else:
                    await spool.close()

    async def reset_data_pool(self):
        """群组授权变更后重建数据面池: SET ROLE 的成员关系是连接级快照, 必须换新连接生效。"""
        await self.close()

    async def ensure_project(self, project: str):
        """Ensure a project schema once per process and event loop.

        Schema/表 DDL 以系统面 (登录角色) 执行, 普通账户角色无建表权;
        建成后若 ACL 已引导, 为未挂组的 legacy 项目补公共只读读写授权。
        """
        await self.connect()
        if project in self._project_ready:
            return
        async with self._get_lock(f"project:{project}"):
            if project in self._project_ready:
                return
            spool = await self._acquire_system()
            async with spool.acquire() as conn:
                await conn.execute("SELECT shared.init_project_schema($1)", project)
                if self._acl_bootstrapped:
                    await self._ensure_schema_open_grants(conn, project)
            self._project_ready.add(project)

    async def register_project(
        self,
        slug: str,
        title: str,
        description: str | None = None,
        schema_name: str | None = None
    ) -> Project:
        """注册新项目：初始化物理 Schema 并登记至 shared.projects 元数据中心 (系统面特权操作)。"""
        await self.connect()
        s_name = schema_name or slug_to_schema_name(slug)
        spool = await self._acquire_system()
        async with spool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS shared.projects (
                    slug VARCHAR(64) PRIMARY KEY,
                    schema_name VARCHAR(32) NOT NULL UNIQUE,
                    title VARCHAR(256) NOT NULL,
                    description TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_projects_schema ON shared.projects(schema_name);
            """)
            await conn.execute("SELECT shared.init_project_schema($1)", s_name)
            if self._acl_bootstrapped:
                await self._ensure_schema_open_grants(conn, s_name)
            # 若已存在同名 schema_name 但 slug 不同的历史记录，做幂等保护更新
            existing_by_schema = await conn.fetchval(
                "SELECT slug FROM shared.projects WHERE schema_name = $1", s_name
            )
            target_slug = existing_by_schema or slug

            row = await conn.fetchrow("""
                INSERT INTO shared.projects (slug, schema_name, title, description)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (slug) DO UPDATE
                SET schema_name = EXCLUDED.schema_name,
                    title = EXCLUDED.title,
                    description = COALESCE(EXCLUDED.description, shared.projects.description),
                    updated_at = CURRENT_TIMESTAMP
                RETURNING slug, schema_name, title, description, created_at, updated_at;
            """, target_slug, s_name, title, description)
            return Project(**dict(row))

    async def update_project(
        self,
        identifier: str,
        title: str | None = None,
        description: str | None = None,
    ) -> Project | None:
        """更新项目元数据 (title, description)。通过 slug 或 schema_name 匹配。"""
        await self.connect()
        proj = await self.get_project(identifier)
        if not proj:
            return None

        spool = await self._acquire_system()
        async with spool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE shared.projects
                SET title = COALESCE($2, title),
                    description = COALESCE($3, description),
                    updated_at = CURRENT_TIMESTAMP
                WHERE slug = $1 OR schema_name = $1
                RETURNING slug, schema_name, title, description, created_at, updated_at;
            """, proj.slug, title, description)
            if row:
                return Project(**dict(row))
            return None

    async def get_project(self, identifier: str) -> Project | None:
        """按 slug (如 bpfio/brix) 或 schema_name (如 brix) 获取项目元数据。"""
        await self.connect()
        async with self._pool.acquire() as conn:
            has_table = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'shared' AND table_name = 'projects'
                );
            """)
            if has_table:
                row = await conn.fetchrow("""
                    SELECT slug, schema_name, title, description, created_at, updated_at
                    FROM shared.projects
                    WHERE slug = $1 OR schema_name = $1
                    LIMIT 1;
                """, identifier)
                if row:
                    return Project(**dict(row))

            # 兼容模式：若 shared.projects 未登记该 slug，但已存在同名物理 Schema
            # 或者根据 slug_to_schema_name 派生的 schema 存在，则向下兼容映射
            candidates = [identifier.lower()]
            try:
                cand = slug_to_schema_name(identifier)
                if cand not in candidates:
                    candidates.append(cand)
            except Exception:
                pass

            for sch in candidates:
                has_schema = await conn.fetchval("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.schemata
                        WHERE schema_name = $1
                    );
                """, sch)
                if has_schema:
                    return Project(
                        slug=identifier,
                        schema_name=sch,
                        title=identifier,
                        description="系统内置/历史初始化项目"
                    )
            return None

    async def list_projects(self) -> list[Project]:
        """获取所有已注册项目元数据列表。"""
        await self.connect()
        async with self._pool.acquire() as conn:
            projects: dict[str, Project] = {}
            has_table = await conn.fetchval("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'shared' AND table_name = 'projects'
                );
            """)
            if has_table:
                rows = await conn.fetch("""
                    SELECT slug, schema_name, title, description, created_at, updated_at
                    FROM shared.projects
                    ORDER BY created_at ASC;
                """)
                for r in rows:
                    p = Project(**dict(r))
                    projects[p.schema_name] = p

            schemata = await conn.fetch("""
                SELECT schema_name 
                FROM information_schema.schemata 
                WHERE schema_name NOT IN ('pg_catalog', 'information_schema', 'shared', 'public')
                  AND schema_name NOT LIKE 'pg_%'
                ORDER BY schema_name;
            """)
            for s in schemata:
                s_name = s["schema_name"].lower()
                if s_name not in projects:
                    projects[s_name] = Project(
                        slug=s_name,
                        schema_name=s_name,
                        title=s_name,
                        description="物理 Schema (未登记 Git 坐标)"
                    )
            return list(projects.values())

    # =========================================================================
    # P1-1: 项目简报 digest (供 mcp brief 工具消费，单包 ≤2K token)
    # =========================================================================

    async def brief_project(self, project_id: str, devlog_limit: int = 5) -> dict:
        """一次查询拼装项目全貌简报 (OPT-03 & OPT-07: 单次单表聚合，消灭 5 次网络 RTT)。

        返回 dict，键固定 (跨线接口契约，不得更改):
          - tasks_open:     未结任务 (planned/running/blocked) 精简列 list[dict]
          - findings_open:  未闭环发现 (open/infix/blocked) 精简列 list[dict]
          - waitings_open:  待办/阻塞中 (open) 精简列 list[dict]
          - batch_running:  running 批次 list[dict]
          - recent_devlogs: 最近 N 条手记 (id/task_id/title/updated_at) list[dict]
        """
        await self.connect()
        n = self._clamp_limit(devlog_limit, default=5)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT dataset_code, doc_no AS id, entity_name, status, payload, updated_at
                FROM shared.records
                WHERE project_id = $1
                  AND (
                      (dataset_code = 'sys_task' AND status IN ('planned', 'running', 'blocked'))
                   OR (dataset_code = 'sys_finding' AND status IN ('open', 'infix', 'blocked'))
                   OR (dataset_code = 'sys_waiting' AND status = 'open')
                   OR (dataset_code = 'sys_batch' AND status = 'running')
                   OR (dataset_code = 'sys_devlog')
                  )
                ORDER BY updated_at DESC;
            """, project_id)

        tasks_open = []
        findings_open = []
        waitings_open = []
        batch_running = []
        recent_devlogs = []

        for r in rows:
            d_code = r["dataset_code"]
            p = r["payload"] if isinstance(r["payload"], dict) else json.loads(r["payload"] or "{}")
            if d_code == "sys_task":
                tasks_open.append({
                    "id": r["id"],
                    "title": r["entity_name"],
                    "status": r["status"],
                    "priority": p.get("priority", "P2"),
                    "assignee": p.get("assignee"),
                    "updated_at": r["updated_at"],
                })
            elif d_code == "sys_finding":
                findings_open.append({
                    "id": r["id"],
                    "severity": p.get("severity", "P2"),
                    "status": r["status"],
                    "task_id": p.get("task_id"),
                    "summary": p.get("summary") or r["entity_name"],
                })
            elif d_code == "sys_waiting":
                waitings_open.append({
                    "id": r["id"],
                    "category": p.get("category", "user"),
                    "owner": p.get("owner", "user"),
                    "description": p.get("description") or r["entity_name"],
                })
            elif d_code == "sys_batch":
                batch_running.append({
                    "id": r["id"],
                    "title": r["entity_name"],
                })
            elif d_code == "sys_devlog" and len(recent_devlogs) < n:
                recent_devlogs.append({
                    "id": p.get("devlog_id") or r["id"],
                    "task_id": p.get("task_id"),
                    "title": r["entity_name"],
                    "updated_at": r["updated_at"],
                })

        return {
            "tasks_open": tasks_open,
            "findings_open": findings_open,
            "waitings_open": waitings_open,
            "batch_running": batch_running,
            "recent_devlogs": recent_devlogs,
        }

    # =========================================================================
    # 多 Agent 对讲信箱与代码文件租约 (0.3.0)
    # =========================================================================

    # =========================================================================
    # 多用户分组权限 (0.5.0): 引导 / 账户 / 群组 / 项目挂载
    # 语义 (方案 A): 组内互见互写, 跨组不可见; 未挂组项目 legacy 全通; admin 全见。
    # 全部特权操作走系统面连接, 数据面仅经降权角色访问。
    # =========================================================================

    async def _ensure_schema_open_grants(self, conn: asyncpg.Connection, schema: str):
        """legacy 开放授权: 未挂组项目对全体认证账户 (logbook_authenticated) 开放读写。

        已挂组项目跳过 (挂载即收口, 由 grant_group_project 精确授权)。
        """
        attached = await conn.fetchval(
            "SELECT EXISTS (SELECT 1 FROM shared.project_groups WHERE schema_name = $1)", schema
        )
        if attached:
            return
        auth = self._qi("logbook_authenticated")
        s = self._qi(schema)
        u = self._qi(self.user)
        await conn.execute(f"""
            GRANT USAGE ON SCHEMA {s} TO {auth};
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {s} TO {auth};
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {s} TO {auth};
            ALTER DEFAULT PRIVILEGES FOR ROLE {u} IN SCHEMA {s}
                GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {auth};
            ALTER DEFAULT PRIVILEGES FOR ROLE {u} IN SCHEMA {s}
                GRANT USAGE, SELECT ON SEQUENCES TO {auth};
        """)

    async def ensure_acl_bootstrap(self):
        """ACL 基座引导 (系统面, 幂等): 公共认证角色 + shared 面授权 + 存量 schema 开放授权。

        迁移 09 已建 ACL 表与 RLS; 本函数负责依赖执行者权限的角色/GRANT 面:
        - logbook_authenticated (NOLOGIN): 全体账户角色的公共权限锚点
        - shared 面对象授权 + 默认权限 (未来新表自动继承)
        - 存量项目 schema 一次性开放授权 (legacy 兼容, 挂组时收回)
        任何失败向上抛出, 由调用方降级 legacy。
        """
        if self._acl_bootstrapped:
            return
        spool = await self._acquire_system()
        async with spool.acquire() as conn:
            role_exists = await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'logbook_authenticated')"
            )
            if not role_exists:
                await conn.execute("CREATE ROLE logbook_authenticated NOLOGIN")
            auth = self._qi("logbook_authenticated")
            u = self._qi(self.user)
            await conn.execute(f"GRANT USAGE ON SCHEMA shared TO {auth};")
            # shared 面数据表 (逐表守卫, 缺表跳过以兼容未迁移库)
            for table, privs in (
                ("principals", "SELECT"),
                ("projects", "SELECT"),
                ("agent_messages", "SELECT, INSERT, UPDATE"),
                ("file_leases", "SELECT, INSERT, UPDATE, DELETE"),
                ("rules", "SELECT, INSERT, UPDATE"),
                ("devlogs", "SELECT, INSERT, UPDATE"),
                ("researches", "SELECT, INSERT, UPDATE"),
                ("datasets", "SELECT, INSERT, UPDATE"),
                ("records", "SELECT, INSERT, UPDATE, DELETE"),
            ):
                if await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", f"shared.{table}"):
                    await conn.execute(f"GRANT {privs} ON shared.{self._qi(table)} TO {auth};")
            await conn.execute(f"GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA shared TO {auth};")
            await conn.execute(f"""
                ALTER DEFAULT PRIVILEGES FOR ROLE {u} IN SCHEMA shared
                    GRANT SELECT, INSERT, UPDATE ON TABLES TO {auth};
                ALTER DEFAULT PRIVILEGES FOR ROLE {u} IN SCHEMA shared
                    GRANT USAGE, SELECT ON SEQUENCES TO {auth};
            """)
            # 存量项目 schema 开放授权 (幂等, 已挂组跳过)
            schemas = await conn.fetch("""
                SELECT nspname FROM pg_namespace
                WHERE nspname NOT IN ('pg_catalog', 'information_schema', 'shared', 'public')
                  AND nspname NOT LIKE 'pg_%';
            """)
            for row in schemas:
                await self._ensure_schema_open_grants(conn, row["nspname"])
        self._acl_bootstrapped = True

    async def ensure_principal(self, agent_name: str | None = None, is_admin: bool | None = None) -> dict:
        """注册/刷新账户并同步 PG 角色 (系统面, 幂等)。

        - principals 行: LOGBOOK_ADMIN_AGENTS 环境变量中的身份强制置 admin
        - PG 账户角色 lb_u_<name> (NOLOGIN, INHERIT) + 公共角色成员
        - 群组成员关系同步: principal_groups → GRANT 群组角色
        成功后标记 _identity_active, 数据面连接 init 钩子开始执行 SET ROLE。
        """
        name = (agent_name or self.identity or "").strip()
        if not name:
            raise ValueError("principal agent_name is required")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name):
            raise ValueError(f"invalid principal name: {name!r}")
        admin_env = {
            a.strip() for a in os.getenv("LOGBOOK_ADMIN_AGENTS", "").split(",") if a.strip()
        }
        want_admin = True if name in admin_env else (bool(is_admin) if is_admin is not None else None)

        spool = await self._acquire_system()
        async with spool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO shared.principals (agent_name, is_admin)
                VALUES ($1, COALESCE($2, FALSE))
                ON CONFLICT (agent_name) DO UPDATE SET
                    is_admin = COALESCE($2, shared.principals.is_admin),
                    updated_at = CURRENT_TIMESTAMP
                RETURNING agent_name, is_admin;
            """, name, want_admin)

            user_role = self._qi(self.pg_role_name("u", name))
            if not await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)",
                self.pg_role_name("u", name),
            ):
                await conn.execute(f"CREATE ROLE {user_role} NOLOGIN INHERIT")
            else:
                await conn.execute(f"ALTER ROLE {user_role} NOLOGIN INHERIT")

            if await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'logbook_authenticated')"
            ):
                await conn.execute(
                    f"GRANT logbook_authenticated TO {user_role}"
                )

            # 群组角色成员同步 (角色缺失则重建, 兼容手工 drop)
            memberships = await conn.fetch("""
                SELECT g.id, g.group_name FROM shared.principal_groups m
                JOIN shared.groups g ON g.id = m.group_id
                WHERE m.agent_name = $1;
            """, name)
            for g in memberships:
                grp_role_name = self.pg_role_name("g", g["group_name"])
                if not await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)", grp_role_name
                ):
                    await conn.execute(f"CREATE ROLE {self._qi(grp_role_name)} NOLOGIN NOINHERIT")
                await conn.execute(
                    f"GRANT {self._qi(grp_role_name)} TO {user_role}"
                )

        if name == (self.identity or ""):
            self._identity_active = True
        return dict(row)

    async def is_admin(self, agent_name: str) -> bool:
        await self.connect()
        async with self._pool.acquire() as conn:
            return bool(await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM shared.principals WHERE agent_name = $1 AND is_admin)",
                agent_name,
            ))

    async def principal_exists(self, agent_name: str) -> bool:
        """收件人账户存在性校验 (SECURITY DEFINER, 数据面可调, 不暴露 principals 全表)。"""
        await self.connect()
        async with self._pool.acquire() as conn:
            return bool(await conn.fetchval(
                "SELECT shared.acl_principal_exists($1)", agent_name
            ))

    async def probe_project(self, schema_name: str, agent: str | None = None) -> dict:
        """系统面探测项目物理存在性与指定身份可见性 (供 project_init 语义化报错)。"""
        spool = await self._acquire_system()
        async with spool.acquire() as conn:
            exists = bool(await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM information_schema.schemata WHERE schema_name = $1)",
                schema_name,
            ))
            visible = bool(await conn.fetchval(
                "SELECT shared.acl_project_visible($1, $2)", schema_name, agent
            )) if exists else False
            return {"exists": exists, "visible": visible}

    async def create_group(self, group_name: str, description: str | None, creator: str | None) -> dict:
        """创建群组 + 对应 PG 群组角色 (系统面, mcp 层 admin 门禁)。"""
        spool = await self._acquire_system()
        async with spool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO shared.groups (group_name, description, created_by)
                VALUES ($1, $2, $3)
                ON CONFLICT (group_name) DO UPDATE SET description = COALESCE(EXCLUDED.description, shared.groups.description)
                RETURNING id, group_name, description, created_by, created_at;
            """, group_name, description, creator)
            role_name = self.pg_role_name("g", group_name)
            if not await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)", role_name
            ):
                await conn.execute(f"CREATE ROLE {self._qi(role_name)} NOLOGIN NOINHERIT")
            return dict(row)

    async def list_groups(self) -> list[dict]:
        """群组列表: 成员与挂载项目汇总 (系统面元数据读, 成员关系表不对数据面授权)。"""
        spool = await self._acquire_system()
        async with spool.acquire() as conn:
            groups = await conn.fetch("""
                SELECT g.id, g.group_name, g.description, g.created_by, g.created_at,
                       COALESCE(m.members, '{}') AS members,
                       COALESCE(p.schemas, '{}') AS schemas
                FROM shared.groups g
                LEFT JOIN LATERAL (
                    SELECT array_agg(mg.agent_name ORDER BY mg.agent_name) AS members
                    FROM shared.principal_groups mg WHERE mg.group_id = g.id
                ) m ON TRUE
                LEFT JOIN LATERAL (
                    SELECT array_agg(pg.schema_name ORDER BY pg.schema_name) AS schemas
                    FROM shared.project_groups pg WHERE pg.group_id = g.id
                ) p ON TRUE
                ORDER BY g.group_name;
            """)
            return [dict(r) for r in groups]

    async def _group_id(self, conn: asyncpg.Connection, group_name: str) -> int:
        gid = await conn.fetchval(
            "SELECT id FROM shared.groups WHERE group_name = $1", group_name
        )
        if gid is None:
            raise ValueError(f"group not found: {group_name}")
        return gid

    async def grant_group_project(self, group_name: str, schema_name: str, operator: str | None) -> None:
        """项目挂载到群组 (挂载即收口): 挂组并精确授权, 同时收回公共开放授权。"""
        spool = await self._acquire_system()
        async with spool.acquire() as conn:
            gid = await self._group_id(conn, group_name)
            await conn.execute("""
                INSERT INTO shared.project_groups (schema_name, group_id, granted_by)
                VALUES ($1, $2, $3)
                ON CONFLICT (schema_name, group_id) DO NOTHING;
            """, schema_name, gid, operator)
            await self._apply_schema_group_grants(conn, schema_name)

    async def revoke_group_project(self, group_name: str, schema_name: str, operator: str | None) -> None:
        """项目摘除群组: 若摘后无任何挂载组, 项目回到 legacy 开放态。"""
        spool = await self._acquire_system()
        async with spool.acquire() as conn:
            gid = await self._group_id(conn, group_name)
            await conn.execute(
                "DELETE FROM shared.project_groups WHERE schema_name = $1 AND group_id = $2",
                schema_name, gid,
            )
            await self._apply_schema_group_grants(conn, schema_name)

    async def _apply_schema_group_grants(self, conn: asyncpg.Connection, schema: str):
        """重算项目 schema 授权面: 无挂组 → 开放; 有挂组 → 仅挂载群组角色 (含默认权限)。"""
        rows = await conn.fetch("""
            SELECT g.group_name FROM shared.project_groups pg
            JOIN shared.groups g ON g.id = pg.group_id
            WHERE pg.schema_name = $1 ORDER BY g.group_name;
        """, schema)
        auth = self._qi("logbook_authenticated")
        s = self._qi(schema)
        u = self._qi(self.user)
        if not rows:
            await conn.execute(f"GRANT USAGE ON SCHEMA {s} TO {auth};")
            await self._ensure_schema_open_grants(conn, schema)
            return
        # 收口: 先收回公共授权
        await conn.execute(f"""
            REVOKE ALL ON SCHEMA {s} FROM {auth};
            REVOKE ALL ON ALL TABLES IN SCHEMA {s} FROM {auth};
            REVOKE ALL ON ALL SEQUENCES IN SCHEMA {s} FROM {auth};
            ALTER DEFAULT PRIVILEGES FOR ROLE {u} IN SCHEMA {s}
                REVOKE ALL ON TABLES FROM {auth};
            ALTER DEFAULT PRIVILEGES FOR ROLE {u} IN SCHEMA {s}
                REVOKE ALL ON SEQUENCES FROM {auth};
        """)
        # 再按挂载群组逐一授权
        grp_roles = [self.pg_role_name("g", r["group_name"]) for r in rows]
        for role_name in grp_roles:
            if not await conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = $1)", role_name
            ):
                await conn.execute(f"CREATE ROLE {self._qi(role_name)} NOLOGIN NOINHERIT")
            q = self._qi(role_name)
            await conn.execute(f"GRANT USAGE ON SCHEMA {s} TO {q};")
        await conn.execute(f"""
            GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {s} TO {', '.join(self._qi(r) for r in grp_roles)};
            GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {s} TO {', '.join(self._qi(r) for r in grp_roles)};
            ALTER DEFAULT PRIVILEGES FOR ROLE {u} IN SCHEMA {s}
                GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {', '.join(self._qi(r) for r in grp_roles)};
            ALTER DEFAULT PRIVILEGES FOR ROLE {u} IN SCHEMA {s}
                GRANT USAGE, SELECT ON SEQUENCES TO {', '.join(self._qi(r) for r in grp_roles)};
        """)
        # 摘除已不在挂载列表中的群组角色残留授权
        for role_name in await self._all_group_role_names(conn):
            if role_name not in grp_roles:
                q = self._qi(role_name)
                await conn.execute(f"""
                    REVOKE ALL ON SCHEMA {s} FROM {q};
                    REVOKE ALL ON ALL TABLES IN SCHEMA {s} FROM {q};
                    REVOKE ALL ON ALL SEQUENCES IN SCHEMA {s} FROM {q};
                    ALTER DEFAULT PRIVILEGES FOR ROLE {u} IN SCHEMA {s}
                        REVOKE ALL ON TABLES FROM {q};
                    ALTER DEFAULT PRIVILEGES FOR ROLE {u} IN SCHEMA {s}
                        REVOKE ALL ON SEQUENCES FROM {q};
                """)

    async def _all_group_role_names(self, conn: asyncpg.Connection) -> list[str]:
        rows = await conn.fetch("SELECT group_name FROM shared.groups ORDER BY group_name")
        return [self.pg_role_name("g", r["group_name"]) for r in rows]

    async def add_group_member(self, group_name: str, agent_name: str, operator: str | None) -> None:
        """加人进群: 登记 principals + principal_groups 并 GRANT 群组角色。"""
        await self.ensure_principal(agent_name)
        spool = await self._acquire_system()
        async with spool.acquire() as conn:
            gid = await self._group_id(conn, group_name)
            await conn.execute("""
                INSERT INTO shared.principal_groups (agent_name, group_id, granted_by)
                VALUES ($1, $2, $3)
                ON CONFLICT (agent_name, group_id) DO NOTHING;
            """, agent_name, gid, operator)
            grp_role = self._qi(self.pg_role_name("g", group_name))
            user_role = self._qi(self.pg_role_name("u", agent_name))
            await conn.execute(f"GRANT {grp_role} TO {user_role}")

    async def remove_group_member(self, group_name: str, agent_name: str, operator: str | None) -> bool:
        """移出群组: 删关系行并 REVOKE 群组角色。返回是否确有移除。"""
        spool = await self._acquire_system()
        async with spool.acquire() as conn:
            gid = await self._group_id(conn, group_name)
            deleted = await conn.fetchval(
                "DELETE FROM shared.principal_groups WHERE agent_name = $1 AND group_id = $2 RETURNING 1",
                agent_name, gid,
            )
            if deleted:
                grp_role = self._qi(self.pg_role_name("g", group_name))
                user_role = self._qi(self.pg_role_name("u", agent_name))
                await conn.execute(f"REVOKE {grp_role} FROM {user_role}")
            return bool(deleted)

    async def get_principal_groups(self, agent_name: str) -> list[str]:
        spool = await self._acquire_system()
        async with spool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT g.group_name FROM shared.principal_groups m
                JOIN shared.groups g ON g.id = m.group_id
                WHERE m.agent_name = $1 ORDER BY g.group_name;
            """, agent_name)
            return [r["group_name"] for r in rows]

    async def ensure_messaging_schema(self):
        """Verify the migration-managed messaging and lease schema once per loop."""
        await self.connect()
        if self._messaging_schema_ensured:
            return
        async with self._get_lock("messaging_schema"):
            if self._messaging_schema_ensured:
                return
            async with self._pool.acquire() as conn:
                ready = bool(await conn.fetchval("""
                    SELECT to_regclass('shared.agent_messages') IS NOT NULL
                       AND to_regclass('shared.file_leases') IS NOT NULL
                       AND EXISTS (
                           SELECT 1 FROM information_schema.columns
                           WHERE table_schema = 'shared'
                             AND table_name = 'agent_messages'
                             AND column_name = 'read_by_agent'
                       )
                """))
            if not ready:
                raise RuntimeError(
                    "messaging schema is not migrated; run python -m logbook.migrate up"
                )
            self._messaging_schema_ensured = True

    async def send_agent_message(
        self,
        project_id: str,
        from_agent: str,
        to_agent: str,
        subject: str,
        content: str,
        task_id: str | None = None,
        thread_id: str | None = None,
        from_ip: str = "0.0.0.0",
        to_ip: str = "0.0.0.0",
    ) -> AgentMessage:
        await self.ensure_messaging_schema()
        now = get_beijing_now()
        async with self._pool.acquire() as conn:
            # 收件箱余量防灌爆 (SECURITY DEFINER 函数, 发件人视角也可校验收件人余量)
            has_room = await conn.fetchval(
                "SELECT shared.acl_mailbox_has_room($1)", to_agent
            )
            if not has_room:
                from .errors import ToolError
                raise ToolError(
                    "RECIPIENT_MAILBOX_FULL",
                    f"收件人 [{to_agent}] 的未读信箱已达容量上限, 投递被拒收; 请其先查收清理信箱。",
                )
            # 生成新 message_id 并统一落入 shared.records
            msg_id = await conn.fetchval("""
                SELECT COALESCE(MAX((payload->>'message_id')::bigint), 0) + 1
                FROM shared.records WHERE dataset_code = 'sys_message';
            """)
            doc_no = f"MSG-{msg_id}"
            actual_thread_id = thread_id or f"t-{msg_id}"
            payload = {
                "message_id": msg_id,
                "from_agent": from_agent,
                "from_ip": from_ip,
                "to_agent": to_agent,
                "to_ip": to_ip,
                "subject": subject,
                "content": content,
                "task_id": task_id,
                "thread_id": actual_thread_id,
                "is_read": False,
                "read_at": None,
                "read_by_agent": None,
                "is_archived": False,
                "archived_at": None,
            }
            ev_date = now.date()
            await conn.execute("""
                INSERT INTO shared.records (
                    project_id, dataset_code, doc_no, entity_name, status, event_date, payload, created_at, updated_at
                ) VALUES (
                    $1, 'sys_message', $2, $3, 'unread', $4, $5::jsonb, $6, $7
                );
            """, project_id, doc_no, subject[:255], ev_date, json.dumps(payload, ensure_ascii=False), now, now)

            return AgentMessage(
                id=msg_id,
                project_id=project_id,
                from_agent=from_agent,
                from_ip=from_ip,
                to_agent=to_agent,
                to_ip=to_ip,
                subject=subject,
                content=content,
                task_id=task_id,
                thread_id=actual_thread_id,
                is_read=False,
                read_at=None,
                created_at=now,
            )

    async def get_agent_inbox(
        self,
        project_id: str | None,
        agent_name: str,
        unread_only: bool = True,
        limit: int = 20,
        include_archived: bool = False,
    ) -> list[AgentMessage]:
        await self.ensure_messaging_schema()
        limit = self._clamp_limit(limit, default=20)
        conditions = ["to_agent = $1", "is_archived = FALSE"]
        params: list[Any] = [agent_name]

        if unread_only:
            conditions.append("is_read = FALSE")
        if include_archived:
            conditions.pop(1)
        if project_id:
            params.append(project_id)
            conditions.append(f"project_id = ${len(params)}")

        where_clause = " AND ".join(conditions)
        params.append(limit)
        query = f"""
            SELECT id, project_id, from_agent, from_ip, to_agent, to_ip, subject, content,
                   task_id, thread_id, is_read, read_at, created_at
            FROM shared.agent_messages
            WHERE {where_clause}
            ORDER BY created_at DESC
            LIMIT ${len(params)};
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [AgentMessage(**dict(r)) for r in rows]

    async def get_agent_message(
        self,
        message_id: int,
        agent_name: str,
    ) -> AgentMessage | None:
        """按 id 取单封信 (仅收发双方可见, RLS 双保险)。"""
        await self.ensure_messaging_schema()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT id, project_id, from_agent, from_ip, to_agent, to_ip, subject, content,
                       task_id, thread_id, is_read, read_at, created_at
                FROM shared.agent_messages
                WHERE id = $1 AND ($2 = '' OR from_agent = $2 OR to_agent = $2);
            """, message_id, agent_name)
            return AgentMessage(**dict(row)) if row else None

    async def reply_agent_message(
        self,
        message_id: int,
        me: str,
        content: str,
        subject: str | None = None,
        from_ip: str = "0.0.0.0",
        to_ip: str = "0.0.0.0",
    ) -> AgentMessage | None:
        """回复信件: 沿用父信 thread_id/project/task, 收件人取对方 (父信发件人或收件人)。

        父信不存在或非收发双方 → None (RLS 语义: 不可见即不存在)。
        """
        parent = await self.get_agent_message(message_id, me)
        if parent is None:
            return None
        other = parent.to_agent if me == parent.from_agent else parent.from_agent
        thread_id = parent.thread_id or f"t-{parent.id}"
        # 父信无线索时物化 thread_id, 保证会话视图能拉到根信 (仅收发双方可执行)
        if not parent.thread_id:
            msg_key = str(parent.id)
            async with self._pool.acquire() as conn:
                await conn.execute("""
                    UPDATE shared.records
                    SET payload = jsonb_set(payload, '{thread_id}', to_jsonb($2::text))
                    WHERE dataset_code = 'sys_message'
                      AND (doc_no = 'MSG-' || $1 OR payload->>'message_id' = $1)
                      AND (payload->>'thread_id' IS NULL OR payload->>'thread_id' = '');
                """, msg_key, thread_id)
        final_subject = subject or (parent.subject if parent.subject.startswith("Re:") else f"Re: {parent.subject}")
        return await self.send_agent_message(
            project_id=parent.project_id,
            from_agent=me,
            to_agent=other,
            subject=final_subject[:256],
            content=content,
            task_id=parent.task_id,
            thread_id=thread_id,
            from_ip=from_ip,
            to_ip=to_ip,
        )

    async def get_agent_thread(
        self,
        thread_id: str,
        agent_name: str,
        project_id: str | None = None,
        limit: int = 50,
    ) -> list[AgentMessage]:
        """拉取会话线索 (仅收发双方可见, 时间正序)。"""
        await self.ensure_messaging_schema()
        limit = self._clamp_limit(limit, default=50)
        conditions = ["thread_id = $1", "is_archived = FALSE",
                      "(from_agent = $2 OR to_agent = $2)"]
        params: list[Any] = [thread_id, agent_name]
        if project_id:
            params.append(project_id)
            conditions.append(f"project_id = ${len(params)}")
        params.append(limit)
        query = f"""
            SELECT id, project_id, from_agent, from_ip, to_agent, to_ip, subject, content,
                   task_id, thread_id, is_read, read_at, created_at
            FROM shared.agent_messages
            WHERE {" AND ".join(conditions)}
            ORDER BY created_at ASC, id ASC
            LIMIT ${len(params)};
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [AgentMessage(**dict(r)) for r in rows]

    async def get_agent_sent(
        self,
        agent_name: str,
        project_id: str | None = None,
        limit: int = 20,
    ) -> list[AgentMessage]:
        """已发送箱。"""
        await self.ensure_messaging_schema()
        limit = self._clamp_limit(limit, default=20)
        conditions = ["from_agent = $1"]
        params: list[Any] = [agent_name]
        if project_id:
            params.append(project_id)
            conditions.append(f"project_id = ${len(params)}")
        params.append(limit)
        query = f"""
            SELECT id, project_id, from_agent, from_ip, to_agent, to_ip, subject, content,
                   task_id, thread_id, is_read, read_at, created_at
            FROM shared.agent_messages
            WHERE {" AND ".join(conditions)}
            ORDER BY created_at DESC
            LIMIT ${len(params)};
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [AgentMessage(**dict(r)) for r in rows]

    async def search_agent_messages(
        self,
        agent_name: str,
        query: str,
        project_id: str | None = None,
        limit: int = 20,
    ) -> list[AgentMessage]:
        """信件全文模糊检索 (仅收发双方可见, pg_trgm ILIKE)。"""
        await self.ensure_messaging_schema()
        limit = self._clamp_limit(limit, default=20)
        conditions = ["(from_agent = $1 OR to_agent = $1)"]
        params: list[Any] = [agent_name]
        if project_id:
            params.append(project_id)
            conditions.append(f"project_id = ${len(params)}")
        if query and query.strip():
            params.append(query.strip())
            q_idx = len(params)
            conditions.append(f"(subject ILIKE '%' || ${q_idx} || '%' OR content ILIKE '%' || ${q_idx} || '%')")
        params.append(limit)
        sql = f"""
            SELECT id, project_id, from_agent, from_ip, to_agent, to_ip, subject, content,
                   task_id, thread_id, is_read, read_at, created_at
            FROM shared.agent_messages
            WHERE {" AND ".join(conditions)}
            ORDER BY created_at DESC
            LIMIT ${len(params)};
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            return [AgentMessage(**dict(r)) for r in rows]

    async def archive_agent_message(
        self,
        message_id: int,
        agent_name: str,
        archived: bool = True,
    ) -> AgentMessage | None:
        """归档/恢复信件 (仅收件人可操作自己的信箱视图)。"""
        await self.ensure_messaging_schema()
        now = get_beijing_now()
        archived_at_str = now.isoformat() if archived else None
        msg_key = str(message_id)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT id, payload, status FROM shared.records
                WHERE dataset_code = 'sys_message'
                  AND (doc_no = 'MSG-' || $1 OR payload->>'message_id' = $1)
                  AND payload->>'to_agent' = $2;
            """, msg_key, agent_name)
            if not row:
                return None
            
            p = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"] or "{}")
            p["is_archived"] = archived
            p["archived_at"] = archived_at_str
            new_status = "archived" if archived else ("read" if p.get("is_read") else "unread")
            await conn.execute("""
                UPDATE shared.records
                SET status = $2, payload = $3::jsonb, updated_at = $4
                WHERE id = $1;
            """, row["id"], new_status, json.dumps(p, ensure_ascii=False), now)

            v_row = await conn.fetchrow("SELECT * FROM shared.v_agent_messages WHERE id = $1;", message_id)
            return AgentMessage(**dict(v_row)) if v_row else None

    async def count_agent_unread(
        self,
        agent_name: str,
        project_id: str | None = None,
    ) -> int:
        """本人未读信件计数 (开工简报/信箱角标)。"""
        await self.ensure_messaging_schema()
        conditions = ["to_agent = $1", "is_read = FALSE", "is_archived = FALSE"]
        params: list[Any] = [agent_name]
        if project_id:
            params.append(project_id)
            conditions.append(f"project_id = ${len(params)}")
        async with self._pool.acquire() as conn:
            return int(await conn.fetchval(
                f"SELECT COUNT(*) FROM shared.v_agent_messages WHERE {' AND '.join(conditions)}",
                *params,
            ))

    async def read_agent_message(
        self,
        message_id: int,
        agent_name: str | None = None,
    ) -> AgentMessage | None:
        recipient = (agent_name or "").strip()
        if not recipient:
            raise ValueError("recipient agent identity is required")
        await self.ensure_messaging_schema()
        now = get_beijing_now()
        msg_key = str(message_id)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT id, payload, status FROM shared.records
                WHERE dataset_code = 'sys_message'
                  AND (doc_no = 'MSG-' || $1 OR payload->>'message_id' = $1)
                  AND payload->>'to_agent' = $2;
            """, msg_key, recipient)
            if not row:
                exists = await conn.fetchval(
                    "SELECT EXISTS (SELECT 1 FROM shared.records WHERE dataset_code = 'sys_message' AND (doc_no = 'MSG-' || $1 OR payload->>'message_id' = $1))",
                    msg_key,
                )
                if exists:
                    logger.warning(
                        "Rejected mailbox read: message_id=%s recipient=%s",
                        message_id,
                        recipient,
                    )
                return None
            
            p = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"] or "{}")
            p["is_read"] = True
            if not p.get("read_at"):
                p["read_at"] = now.isoformat()
            if not p.get("read_by_agent"):
                p["read_by_agent"] = recipient
            new_status = "archived" if p.get("is_archived") else "read"
            await conn.execute("""
                UPDATE shared.records
                SET status = $2, payload = $3::jsonb, updated_at = $4
                WHERE id = $1;
            """, row["id"], new_status, json.dumps(p, ensure_ascii=False), now)

            v_row = await conn.fetchrow("SELECT * FROM shared.v_agent_messages WHERE id = $1;", message_id)
            return AgentMessage(**dict(v_row)) if v_row else None

    async def get_mailbox_summary(
        self,
        project_id: str,
    ) -> list[dict]:
        """获取项目内对讲信箱的未读信件概况与最新时间戳。"""
        await self.ensure_messaging_schema()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT to_agent,
                       COUNT(*)::int AS unread_count,
                       MAX(created_at) AS latest_at,
                       array_agg(id ORDER BY id DESC) AS unread_ids
                FROM shared.agent_messages
                WHERE project_id = $1 AND is_read = FALSE
                GROUP BY to_agent
                ORDER BY latest_at DESC;
            """, project_id)
            return [dict(r) for r in rows]

    async def acquire_file_lease(
        self,
        project_id: str,
        agent_name: str,
        file_path: str,
        duration_seconds: int = 300,
        agent_ip: str = "0.0.0.0",
    ) -> tuple[bool, FileLease | None, str | None]:
        """申请代码文件租约。返回 (success, lease, conflict_agent_if_failed)。"""
        await self.ensure_messaging_schema()
        duration_seconds = max(10, min(duration_seconds, 3600))
        async with self._pool.acquire() as conn:
            result = await conn.fetchrow("""
                WITH attempted AS (
                    INSERT INTO shared.file_leases
                        (project_id, agent_name, agent_ip, file_path, lease_expires_at, created_at)
                    VALUES (
                        $1, $2, $3, $4,
                        CURRENT_TIMESTAMP + ($5 * INTERVAL '1 second'),
                        CURRENT_TIMESTAMP
                    )
                    ON CONFLICT (project_id, file_path) DO UPDATE SET
                        agent_name = EXCLUDED.agent_name,
                        agent_ip = EXCLUDED.agent_ip,
                        lease_expires_at = CURRENT_TIMESTAMP + ($5 * INTERVAL '1 second'),
                        created_at = CURRENT_TIMESTAMP
                    WHERE shared.file_leases.lease_expires_at <= CURRENT_TIMESTAMP
                       OR shared.file_leases.agent_name = EXCLUDED.agent_name
                    RETURNING id, project_id, agent_name, agent_ip, file_path,
                              lease_expires_at, created_at
                ), current AS (
                    SELECT id, project_id, agent_name, agent_ip, file_path,
                           lease_expires_at, created_at
                    FROM shared.file_leases
                    WHERE project_id = $1 AND file_path = $4
                )
                SELECT
                    (SELECT to_jsonb(attempted) FROM attempted) AS attempted,
                    (SELECT to_jsonb(current) FROM current) AS current;
            """, project_id, agent_name, agent_ip, file_path, duration_seconds)
            attempted = json.loads(result["attempted"]) if result["attempted"] else None
            if attempted:
                return True, FileLease(**attempted), None

            conflict_row = json.loads(result["current"]) if result["current"] else None
            if conflict_row:
                return False, FileLease(**conflict_row), conflict_row["agent_name"]
            return False, None, None

    async def release_file_lease(
        self,
        project_id: str,
        agent_name: str,
        file_path: str,
    ) -> bool:
        """释放代码文件租约。"""
        await self.ensure_messaging_schema()
        async with self._pool.acquire() as conn:
            res = await conn.execute("""
                DELETE FROM shared.file_leases
                WHERE project_id = $1 AND file_path = $2 AND agent_name = $3;
            """, project_id, file_path, agent_name)
            return res == "DELETE 1"

    async def query_file_leases(
        self,
        project_id: str,
    ) -> list[FileLease]:
        """查询项目内所有尚未过期的有效文件租约。"""
        await self.ensure_messaging_schema()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT id, project_id, agent_name, agent_ip, file_path, lease_expires_at, created_at
                FROM shared.file_leases
                WHERE project_id = $1 AND lease_expires_at > CURRENT_TIMESTAMP
                ORDER BY lease_expires_at ASC;
            """, project_id)
            return [FileLease(**dict(r)) for r in rows]

    # =========================================================================
    # 通用事实引擎与数据集 (shared.datasets & shared.records) (0.5.0)
    # =========================================================================

    async def ensure_datasets_and_records_schema(self):
        """确保 shared.datasets 与 shared.records 物理表与索引就绪 (幂等)。"""
        await self.connect()
        if self._datasets_and_records_schema_ensured:
            return
        async with self._get_lock("datasets_records_schema"):
            if self._datasets_and_records_schema_ensured:
                return
            spool = await self._acquire_system()
            async with spool.acquire() as conn:
                await conn.execute("""
                CREATE TABLE IF NOT EXISTS shared.datasets (
                    code            VARCHAR(64) PRIMARY KEY,
                    name            VARCHAR(256) NOT NULL,
                    category        VARCHAR(32) NOT NULL DEFAULT 'general',
                    field_schema    JSONB NOT NULL DEFAULT '[]'::jsonb,
                    alias_map       JSONB NOT NULL DEFAULT '{}'::jsonb,
                    description     TEXT,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_datasets_category ON shared.datasets(category);

                CREATE TABLE IF NOT EXISTS shared.records (
                    id              BIGSERIAL PRIMARY KEY,
                    project_id      VARCHAR(64) NOT NULL,
                    dataset_code    VARCHAR(64) NOT NULL REFERENCES shared.datasets(code) ON DELETE RESTRICT,
                    parent_id       BIGINT REFERENCES shared.records(id) ON DELETE CASCADE,
                    batch_id        VARCHAR(64),
                    doc_no          VARCHAR(128) NOT NULL,
                    entity_name     VARCHAR(256),
                    amount          NUMERIC(18, 4) NOT NULL DEFAULT 0,
                    currency        VARCHAR(16) NOT NULL DEFAULT 'CNY',
                    event_date      DATE NOT NULL,
                    status          VARCHAR(32) NOT NULL DEFAULT 'recorded',
                    payload         JSONB NOT NULL DEFAULT '{}'::jsonb,
                    tsv_content     tsvector GENERATED ALWAYS AS (
                        to_tsvector('simple', coalesce(doc_no, '') || ' ' || coalesce(entity_name, '') || ' ' || payload::text)
                    ) STORED,
                    created_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at      TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_records_scope ON shared.records (project_id, dataset_code, event_date);
                CREATE INDEX IF NOT EXISTS idx_records_doc ON shared.records (project_id, doc_no);
                CREATE INDEX IF NOT EXISTS idx_records_parent ON shared.records (parent_id) WHERE parent_id IS NOT NULL;
                CREATE INDEX IF NOT EXISTS idx_records_payload ON shared.records USING GIN (payload jsonb_path_ops);
                CREATE INDEX IF NOT EXISTS idx_records_tsv ON shared.records USING GIN (tsv_content);

                ALTER TABLE shared.records ADD COLUMN IF NOT EXISTS embedding vector(768);
                CREATE INDEX IF NOT EXISTS idx_records_vector_hnsw ON shared.records USING hnsw (embedding vector_cosine_ops)
                    WITH (m = 16, ef_construction = 64);

                ALTER TABLE shared.records ENABLE ROW LEVEL SECURITY;

                DO $policy$
                BEGIN
                    IF EXISTS (
                        SELECT 1 FROM pg_proc nsp_p 
                        JOIN pg_namespace nsp ON nsp.oid = nsp_p.pronamespace 
                        WHERE nsp.nspname = 'shared' AND nsp_p.proname = 'acl_project_visible'
                    ) THEN
                        DROP POLICY IF EXISTS p_records_select ON shared.records;
                        DROP POLICY IF EXISTS p_records_insert ON shared.records;
                        DROP POLICY IF EXISTS p_records_update ON shared.records;
                        DROP POLICY IF EXISTS p_records_delete ON shared.records;

                        CREATE POLICY p_records_select ON shared.records
                            FOR SELECT
                            USING (shared.acl_project_visible(project_id));
                        CREATE POLICY p_records_insert ON shared.records
                            FOR INSERT
                            WITH CHECK (shared.acl_project_visible(project_id));
                        CREATE POLICY p_records_update ON shared.records
                            FOR UPDATE
                            USING (shared.acl_project_visible(project_id))
                            WITH CHECK (shared.acl_project_visible(project_id));
                        CREATE POLICY p_records_delete ON shared.records
                            FOR DELETE
                            USING (shared.acl_project_visible(project_id));
                    END IF;
                END $policy$;

                DO $auth$
                BEGIN
                    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'logbook_authenticated') THEN
                        GRANT SELECT, INSERT, UPDATE ON shared.datasets TO logbook_authenticated;
                        GRANT SELECT, INSERT, UPDATE, DELETE ON shared.records TO logbook_authenticated;
                        GRANT USAGE, SELECT ON SEQUENCE shared.records_id_seq TO logbook_authenticated;
                    END IF;
                END $auth$;
                """)
            self._datasets_and_records_schema_ensured = True

    async def upsert_dataset(self, dataset: Dataset) -> dict:
        """注册或更新数据集元数据规范 (幂等)。"""
        await self.ensure_datasets_and_records_schema()
        async with self._pool.acquire() as conn:
            sql = """
                INSERT INTO shared.datasets (
                    code, name, category, field_schema, alias_map, json_schema, description, created_at, updated_at
                ) VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6::jsonb, $7, $8, $9)
                ON CONFLICT (code) DO UPDATE SET
                    name = EXCLUDED.name,
                    category = EXCLUDED.category,
                    field_schema = EXCLUDED.field_schema,
                    alias_map = EXCLUDED.alias_map,
                    json_schema = COALESCE(EXCLUDED.json_schema, shared.datasets.json_schema),
                    description = COALESCE(EXCLUDED.description, shared.datasets.description),
                    updated_at = EXCLUDED.updated_at
                RETURNING code, name, category, field_schema, alias_map, json_schema, description, created_at, updated_at;
            """
            row = await conn.fetchrow(
                sql,
                dataset.code,
                dataset.name,
                dataset.category,
                json.dumps(dataset.field_schema, ensure_ascii=False),
                json.dumps(dataset.alias_map, ensure_ascii=False),
                json.dumps(dataset.json_schema, ensure_ascii=False) if dataset.json_schema is not None else None,
                dataset.description,
                dataset.created_at,
                dataset.updated_at,
            )
            d = dict(row)
            if isinstance(d.get("field_schema"), str):
                d["field_schema"] = json.loads(d["field_schema"])
            if isinstance(d.get("alias_map"), str):
                d["alias_map"] = json.loads(d["alias_map"])
            if isinstance(d.get("json_schema"), str):
                d["json_schema"] = json.loads(d["json_schema"])
            try:
                from .engine import fact_engine
                fact_engine.invalidate_schema_cache(dataset.code)
            except Exception:
                pass
            return d

    async def get_dataset(self, code: str) -> dict | None:
        """获取单个数据集元数据规范。"""
        await self.ensure_datasets_and_records_schema()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM shared.datasets WHERE code = $1;", code.strip().lower())
            if not row:
                return None
            d = dict(row)
            if isinstance(d.get("field_schema"), str):
                d["field_schema"] = json.loads(d["field_schema"])
            if isinstance(d.get("alias_map"), str):
                d["alias_map"] = json.loads(d["alias_map"])
            if isinstance(d.get("json_schema"), str):
                d["json_schema"] = json.loads(d["json_schema"])
            return d

    async def list_datasets(self, category: str | None = None, limit: int = 50) -> list[dict]:
        """查询数据集定义列表。"""
        await self.ensure_datasets_and_records_schema()
        limit = self._clamp_limit(limit, default=50)
        conditions = []
        params: list[Any] = []
        if category:
            params.append(category.strip().lower())
            conditions.append(f"category = ${len(params)}")
        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        sql = f"""
            SELECT code, name, category, field_schema, alias_map, json_schema, description, created_at, updated_at
            FROM shared.datasets
            {where_clause}
            ORDER BY code ASC
            LIMIT ${len(params)};
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            res = []
            for r in rows:
                d = dict(r)
                if isinstance(d.get("field_schema"), str):
                    d["field_schema"] = json.loads(d["field_schema"])
                if isinstance(d.get("alias_map"), str):
                    d["alias_map"] = json.loads(d["alias_map"])
                res.append(d)
            return res

    async def upsert_record(self, record: DataRecord, patch: bool = False) -> dict:
        """插入或更新单个事实记录 (如果指定 id 或匹配到同单号同主键则更新，支持自动注册未定义数据集)。"""
        await self.ensure_datasets_and_records_schema()
        ev_date = record.event_date
        if isinstance(ev_date, str):
            cleaned = ev_date.strip().replace("/", "-")
            if len(cleaned) == 8 and cleaned.isdigit():
                ev_date = date(int(cleaned[:4]), int(cleaned[4:6]), int(cleaned[6:8]))
            else:
                ev_date = date.fromisoformat(cleaned[:10])

        vec_literal = f"[{','.join(str(x) for x in record.embedding)}]" if record.embedding else None

        async with self._pool.acquire() as conn:
            # 自动登记未注册数据集 (防止外键约束报错)
            await conn.execute("""
                INSERT INTO shared.datasets (code, name, category, field_schema)
                VALUES ($1, $1, 'custom', '[]'::jsonb)
                ON CONFLICT (code) DO NOTHING;
            """, record.dataset_code)

            existing_id = record.id
            existing_row = None
            if existing_id is None:
                if record.doc_no and str(record.doc_no).strip():
                    existing_row = await conn.fetchrow(
                        """
                        SELECT id, doc_no, payload, entity_name FROM shared.records 
                        WHERE project_id = $1 AND dataset_code = $2 AND doc_no = $3 
                          AND parent_id IS NOT DISTINCT FROM $4
                        LIMIT 1;
                        """,
                        record.project_id, record.dataset_code, record.doc_no.strip(), record.parent_id
                    )
                    if existing_row:
                        existing_id = existing_row["id"]
            else:
                existing_row = await conn.fetchrow(
                    """
                    SELECT id, doc_no, payload, entity_name FROM shared.records
                    WHERE id = $1 AND project_id = $2
                    LIMIT 1;
                    """,
                    existing_id, record.project_id
                )
                if not existing_row:
                    raise ValueError(f"指定更新的事实记录 ID [{existing_id}] 在项目 [{record.project_id}] 中不存在！")

            is_patch = patch or getattr(record, "patch", False)

            if existing_id is not None and existing_row is not None:
                final_doc_no = record.doc_no.strip() if record.doc_no and str(record.doc_no).strip() else existing_row["doc_no"]
                if is_patch:
                    old_p = existing_row["payload"]
                    if isinstance(old_p, str):
                        try:
                            old_p = json.loads(old_p)
                        except Exception:
                            old_p = {}
                    elif not isinstance(old_p, dict):
                        old_p = {}
                    final_payload = {**old_p, **(record.payload or {})}
                    from logbook.engine import fact_engine
                    await fact_engine.validate_payload_schema(record.dataset_code, final_payload)
                else:
                    final_payload = record.payload or {}

                update_sql = """
                    UPDATE shared.records
                    SET dataset_code = $2,
                        parent_id = $3,
                        batch_id = COALESCE($4, shared.records.batch_id),
                        doc_no = $5,
                        entity_name = COALESCE($6, shared.records.entity_name),
                        amount = $7,
                        currency = $8,
                        event_date = $9,
                        status = $10,
                        payload = $11::jsonb,
                        embedding = COALESCE($12::vector, shared.records.embedding),
                        updated_at = NOW()
                    WHERE id = $1 AND project_id = $13
                    RETURNING id, project_id, dataset_code, parent_id, batch_id,
                              doc_no, entity_name, amount, currency, event_date,
                              status, payload, created_at, updated_at;
                """
                res = await conn.fetchrow(
                    update_sql,
                    existing_id,
                    record.dataset_code,
                    record.parent_id,
                    record.batch_id,
                    final_doc_no,
                    record.entity_name,
                    record.amount,
                    record.currency,
                    ev_date,
                    record.status,
                    json.dumps(final_payload, ensure_ascii=False),
                    vec_literal,
                    record.project_id,
                )
                if res:
                    d = dict(res)
                    if isinstance(d.get("payload"), str):
                        d["payload"] = json.loads(d["payload"])
                    return d

            # 新增插入分支
            if not record.doc_no or not str(record.doc_no).strip():
                raise ValueError("新增事实单据必须提供有效 doc_no 单据号！")

            final_doc_no = record.doc_no.strip()
            final_payload = record.payload or {}
            if is_patch:
                from logbook.engine import fact_engine
                await fact_engine.validate_payload_schema(record.dataset_code, final_payload)

            insert_sql = """
                INSERT INTO shared.records (
                    project_id, dataset_code, parent_id, batch_id,
                    doc_no, entity_name, amount, currency, event_date,
                    status, payload, embedding, created_at, updated_at
                ) VALUES (
                    $1, $2, $3, $4,
                    $5, $6, $7, $8, $9,
                    $10, $11::jsonb, $12::vector, $13, $14
                )
                RETURNING id, project_id, dataset_code, parent_id, batch_id,
                          doc_no, entity_name, amount, currency, event_date,
                          status, payload, created_at, updated_at;
            """
            res = await conn.fetchrow(
                insert_sql,
                record.project_id,
                record.dataset_code,
                record.parent_id,
                record.batch_id,
                final_doc_no,
                record.entity_name,
                record.amount,
                record.currency,
                ev_date,
                record.status,
                json.dumps(final_payload, ensure_ascii=False),
                vec_literal,
                record.created_at,
                record.updated_at,
            )
            d = dict(res)
            if isinstance(d.get("payload"), str):
                d["payload"] = json.loads(d["payload"])
            return d

    async def bulk_import_records(self, records: list[DataRecord]) -> int:
        """批量导入通用事实数据行 (事务内批处理)。返回成功入库条数。"""
        await self.ensure_datasets_and_records_schema()
        if not records:
            return 0
        insert_sql = """
            INSERT INTO shared.records (
                project_id, dataset_code, parent_id, batch_id,
                doc_no, entity_name, amount, currency, event_date,
                status, payload, embedding, created_at, updated_at
            ) VALUES (
                $1, $2, $3, $4,
                $5, $6, $7, $8, $9,
                $10, $11::jsonb, $12::vector, $13, $14
            );
        """
        count = 0
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for code in list({r.dataset_code for r in records}):
                    await conn.execute("""
                        INSERT INTO shared.datasets (code, name, category, field_schema)
                        VALUES ($1, $1, 'custom', '[]'::jsonb)
                        ON CONFLICT (code) DO NOTHING;
                    """, code)
                for r in records:
                    ev_date = r.event_date
                    if isinstance(ev_date, str):
                        cleaned = ev_date.strip().replace("/", "-")
                        if len(cleaned) == 8 and cleaned.isdigit():
                            ev_date = date(int(cleaned[:4]), int(cleaned[4:6]), int(cleaned[6:8]))
                        else:
                            ev_date = date.fromisoformat(cleaned[:10])
                    vec_literal = f"[{','.join(str(x) for x in r.embedding)}]" if r.embedding else None
                    await conn.execute(
                        insert_sql,
                        r.project_id,
                        r.dataset_code,
                        r.parent_id,
                        r.batch_id,
                        r.doc_no,
                        r.entity_name,
                        r.amount,
                        r.currency,
                        ev_date,
                        r.status,
                        json.dumps(r.payload, ensure_ascii=False),
                        vec_literal,
                        r.created_at,
                        r.updated_at,
                    )
                    count += 1
        return count

    async def bulk_upsert_records(
        self,
        records: list[DataRecord] | str,
        records_alt: list[DataRecord] | None = None,
    ) -> dict:
        """批量写入或更新通用事实单据 (单事务批处理，支持同单号幂等更新)。返回统计字典。"""
        # 兼容 (records) 与历史 (project, records) 双形态调用防呆
        actual_records: list[DataRecord] = records_alt if isinstance(records, str) and records_alt is not None else records  # type: ignore
        await self.ensure_datasets_and_records_schema()
        if not actual_records:
            return {"inserted": 0, "updated": 0, "total": 0}

        inserted_cnt = 0
        updated_cnt = 0

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for code in list({r.dataset_code for r in actual_records}):
                    await conn.execute("""
                        INSERT INTO shared.datasets (code, name, category, field_schema)
                        VALUES ($1, $1, 'custom', '[]'::jsonb)
                        ON CONFLICT (code) DO NOTHING;
                    """, code)

                proj = actual_records[0].project_id
                dataset_codes = list({r.dataset_code for r in actual_records})
                doc_nos = [r.doc_no for r in actual_records]

                # OPT-32: 采用 (dataset_code, doc_no) 复合键匹配，支持同批次多数据集与杜绝跨数据集覆盖
                existing_rows = await conn.fetch(
                    """
                    SELECT id, dataset_code, doc_no FROM shared.records
                    WHERE project_id = $1 AND dataset_code = ANY($2) AND doc_no = ANY($3)
                      AND parent_id IS NULL;
                    """,
                    proj, dataset_codes, doc_nos
                )
                existing_map = {(row["dataset_code"], row["doc_no"]): row["id"] for row in existing_rows}

                insert_sql = """
                    INSERT INTO shared.records (
                        project_id, dataset_code, parent_id, batch_id,
                        doc_no, entity_name, amount, currency, event_date,
                        status, payload, embedding, created_at, updated_at
                    ) VALUES (
                        $1, $2, $3, $4,
                        $5, $6, $7, $8, $9,
                        $10, $11::jsonb, $12::vector, $13, $14
                    );
                """

                update_sql = """
                    UPDATE shared.records
                    SET parent_id = $2,
                        batch_id = COALESCE($3, shared.records.batch_id),
                        entity_name = COALESCE($4, shared.records.entity_name),
                        amount = $5,
                        currency = $6,
                        event_date = $7,
                        status = $8,
                        payload = $9::jsonb,
                        embedding = COALESCE($10::vector, shared.records.embedding),
                        updated_at = NOW()
                    WHERE id = $1 AND project_id = $11;
                """

                for r in actual_records:
                    ev_date = r.event_date
                    if isinstance(ev_date, str):
                        cleaned = ev_date.strip().replace("/", "-")
                        if len(cleaned) == 8 and cleaned.isdigit():
                            ev_date = date(int(cleaned[:4]), int(cleaned[4:6]), int(cleaned[6:8]))
                        else:
                            ev_date = date.fromisoformat(cleaned[:10])

                    vec_literal = f"[{','.join(str(x) for x in r.embedding)}]" if r.embedding else None

                    target_id = existing_map.get((r.dataset_code, r.doc_no)) if r.parent_id is None else None
                    if target_id is not None:
                        await conn.execute(
                            update_sql,
                            target_id,
                            r.parent_id,
                            r.batch_id,
                            r.entity_name,
                            r.amount,
                            r.currency,
                            ev_date,
                            r.status,
                            json.dumps(r.payload, ensure_ascii=False),
                            vec_literal,
                            r.project_id,
                        )
                        updated_cnt += 1
                    else:
                        await conn.execute(
                            insert_sql,
                            r.project_id,
                            r.dataset_code,
                            r.parent_id,
                            r.batch_id,
                            r.doc_no,
                            r.entity_name,
                            r.amount,
                            r.currency,
                            ev_date,
                            r.status,
                            json.dumps(r.payload, ensure_ascii=False),
                            vec_literal,
                            r.created_at,
                            r.updated_at,
                        )
                        inserted_cnt += 1

        return {"inserted": inserted_cnt, "updated": updated_cnt, "total": len(actual_records)}

    async def query_records(
        self,
        project_id: str | None = None,
        dataset_code: str | None = None,
        doc_no: str | None = None,
        entity_name: str | None = None,
        date_from: date | str | None = None,
        date_to: date | str | None = None,
        amount_min: Decimal | float | None = None,
        amount_max: Decimal | float | None = None,
        status: str | list[str] | None = None,
        batch_id: str | None = None,
        parent_id: int | None = None,
        payload_filter: dict | None = None,
        query_text: str | None = None,
        limit: int = 50,
        offset: int = 0,
        project: str | None = None,
        id: int | list[int] | None = None,
    ) -> list[dict]:
        """多维度查询事实数据记录。"""
        actual_proj = (project_id or project or "").strip()
        if not actual_proj:
            raise ValueError("project_id 或 project 必填！")
        await self.ensure_datasets_and_records_schema()
        limit = self._clamp_limit(limit, default=50)
        try:
            offset = max(0, int(offset or 0))
        except (TypeError, ValueError):
            offset = 0

        conditions = ["project_id = $1"]
        params: list[Any] = [actual_proj]

        if id is not None:
            if isinstance(id, (list, tuple, set)):
                id_list = []
                for x in id:
                    if x is not None and str(x).strip().isdigit():
                        id_list.append(int(x))
                if id_list:
                    params.append(id_list)
                    conditions.append(f"id = ANY(${len(params)})")
            else:
                if str(id).strip().isdigit():
                    params.append(int(id))
                    conditions.append(f"id = ${len(params)}")

        if dataset_code:
            params.append(dataset_code.strip().lower())
            conditions.append(f"dataset_code = ${len(params)}")
        if doc_no:
            params.append(doc_no.strip())
            conditions.append(f"doc_no = ${len(params)}")
        if entity_name:
            params.append(f"%{entity_name.strip()}%")
            conditions.append(f"entity_name ILIKE ${len(params)}")
        if date_from:
            d_from = date_from if isinstance(date_from, date) else date.fromisoformat(str(date_from).strip()[:10])
            params.append(d_from)
            conditions.append(f"event_date >= ${len(params)}")
        if date_to:
            d_to = date_to if isinstance(date_to, date) else date.fromisoformat(str(date_to).strip()[:10])
            params.append(d_to)
            conditions.append(f"event_date <= ${len(params)}")
        if amount_min is not None:
            params.append(Decimal(str(amount_min)))
            conditions.append(f"amount >= ${len(params)}")
        if amount_max is not None:
            params.append(Decimal(str(amount_max)))
            conditions.append(f"amount <= ${len(params)}")
        if status:
            if isinstance(status, (list, tuple, set)):
                st_arr = [str(s).strip().lower() for s in status if s and str(s).strip()]
                if st_arr:
                    params.append(st_arr)
                    conditions.append(f"status = ANY(${len(params)})")
            else:
                params.append(str(status).strip().lower())
                conditions.append(f"status = ${len(params)}")
        if batch_id:
            params.append(batch_id.strip())
            conditions.append(f"batch_id = ${len(params)}")
        if parent_id is not None:
            params.append(int(parent_id))
            conditions.append(f"parent_id = ${len(params)}")
        if payload_filter:
            params.append(json.dumps(payload_filter, ensure_ascii=False))
            conditions.append(f"payload @> ${len(params)}::jsonb")

        where_clause = " AND ".join(conditions)

        if query_text and query_text.strip():
            params.append(query_text.strip())
            q_idx = len(params)
            where_clause += f" AND tsv_content @@ plainto_tsquery('simple', ${q_idx})"
            order_clause = f"ORDER BY ts_rank_cd(tsv_content, plainto_tsquery('simple', ${q_idx})) DESC, event_date DESC, id DESC"
        else:
            order_clause = "ORDER BY event_date DESC, id DESC"

        params.append(limit)
        lim_idx = len(params)
        params.append(offset)
        off_idx = len(params)

        sql = f"""
            SELECT id, project_id, dataset_code, parent_id, batch_id,
                   doc_no, entity_name, amount, currency, event_date,
                   status, payload, created_at, updated_at
            FROM shared.records
            WHERE {where_clause}
            {order_clause}
            LIMIT ${lim_idx} OFFSET ${off_idx};
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(sql, *params)
            results = []
            for r in rows:
                d = dict(r)
                if isinstance(d.get("payload"), str):
                    d["payload"] = json.loads(d["payload"])
                results.append(d)
            return results

    async def search_records(
        self,
        project_id: str,
        query_vector: list[float] | None = None,
        query_text: str | None = None,
        dataset_code: str | None = None,
        status: str | list[str] | None = None,
        limit: int = 10,
        offset: int = 0,
        min_score: float | None = None,
        return_mode: bool = False,
    ) -> list[dict] | tuple[list[dict], str]:
        """语义向量与全文混合检索事实数据记录 (支持 768 维 HNSW 余弦与 tsvector 降级)。"""
        await self.ensure_datasets_and_records_schema()
        limit = self._clamp_limit(limit, default=10)
        try:
            offset = max(0, int(offset or 0))
        except (TypeError, ValueError):
            offset = 0

        parsed_min_score: float | None = None
        if min_score is not None:
            try:
                parsed_min_score = max(0.0, min(1.0, float(min_score)))
            except (TypeError, ValueError):
                parsed_min_score = None

        base_conditions = ["project_id = $1"]
        base_params: list[Any] = [project_id]

        if dataset_code:
            base_params.append(dataset_code.strip().lower())
            base_conditions.append(f"dataset_code = ${len(base_params)}")
        if status:
            if isinstance(status, (list, tuple, set)):
                st_arr = [str(s).strip().lower() for s in status if s and str(s).strip()]
                if st_arr:
                    base_params.append(st_arr)
                    base_conditions.append(f"status = ANY(${len(base_params)})")
            else:
                s_val = str(status).strip().lower()
                if s_val:
                    base_params.append(s_val)
                    base_conditions.append(f"status = ${len(base_params)}")

        async with self._pool.acquire() as conn:
            # 探测当前过滤范围下是否存在已建立向量的记录 (F-SEARCH-08)
            has_vectors = False
            if query_vector:
                has_vectors = await conn.fetchval(
                    f"SELECT EXISTS(SELECT 1 FROM shared.records WHERE {' AND '.join(base_conditions)} AND embedding IS NOT NULL);",
                    *base_params,
                )

            # 1. 语义向量检索优先 (仅当当前过滤范围内存在已建向量时执行)
            if query_vector and has_vectors:
                params = list(base_params)
                conditions = list(base_conditions)
                conditions.append("embedding IS NOT NULL")

                vec_literal = f"[{','.join(str(x) for x in query_vector)}]"
                params.append(vec_literal)
                vec_idx = len(params)

                if parsed_min_score is not None:
                    params.append(parsed_min_score)
                    ms_idx = len(params)
                    conditions.append(f"(1 - (embedding <=> ${vec_idx}::vector)) >= ${ms_idx}")

                where_clause = " AND ".join(conditions)

                params.append(limit)
                lim_idx = len(params)
                params.append(offset)
                off_idx = len(params)

                sql = f"""
                    SELECT id, project_id, dataset_code, parent_id, batch_id,
                           doc_no, entity_name, amount, currency, event_date,
                           status, payload, created_at, updated_at,
                           (1 - (embedding <=> ${vec_idx}::vector)) AS score
                    FROM shared.records
                    WHERE {where_clause}
                    ORDER BY embedding <=> ${vec_idx}::vector ASC
                    LIMIT ${lim_idx} OFFSET ${off_idx};
                """
                ef_val = max(40, min(400, (limit + offset) * 2))
                try:
                    await conn.execute(f"SET hnsw.ef_search = {ef_val};")
                except Exception:
                    pass
                rows = await conn.fetch(sql, *params)
                if rows:
                    results = []
                    for r in rows:
                        d = dict(r)
                        if isinstance(d.get("payload"), str):
                            d["payload"] = json.loads(d["payload"])
                        if "score" in d:
                            d["score"] = round(float(d["score"]), 4)
                        if parsed_min_score is not None and d.get("score") is not None:
                            if d["score"] < parsed_min_score:
                                continue
                        results.append(d)
                    return (results, "semantic_vector") if return_mode else results

                # F-SEARCH-06: 该项目已建立向量，若语义向量检索 0 命中且 min_score >= 0.40，严禁静默穿透降级全文导致全文假命中
                if parsed_min_score is not None and parsed_min_score >= 0.40:
                    return ([], "semantic_vector") if return_mode else []

            # 2. 降级分支: 全文关键词检索 (tsvector)
            # 若无 query_vector 或当前范围全量无向量 (not has_vectors) 或向量检索未命中且 min_score < 0.40，且提供了有效 query_text 时尝试全文召回
            if query_text and query_text.strip():
                params = list(base_params)
                conditions = list(base_conditions)
                q_text = query_text.strip()
                params.append(q_text)
                q_idx = len(params)
                conditions.append(f"tsv_content @@ plainto_tsquery('simple', ${q_idx})")

                # 注意: 若是无向量库平滑降级 (query_vector 存在但 not has_vectors)，
                # parsed_min_score 是为向量余弦相似度设定的默认截断阈值，不应用于阻断全文精确匹配 (F-SEARCH-08)
                is_vector_fallback = bool(query_vector and not has_vectors)
                if parsed_min_score is not None and not is_vector_fallback:
                    # 归一化得分 score_norm = raw / (1 + raw) >= min_score => raw >= min_score / (1 - min_score)
                    if parsed_min_score < 1.0:
                        raw_cutoff = parsed_min_score / (1.0 - parsed_min_score)
                    else:
                        raw_cutoff = 9999.0
                    params.append(raw_cutoff)
                    ms_idx = len(params)
                    conditions.append(f"ts_rank_cd(tsv_content, plainto_tsquery('simple', ${q_idx})) >= ${ms_idx}")

                where_clause = " AND ".join(conditions)

                params.append(limit)
                lim_idx = len(params)
                params.append(offset)
                off_idx = len(params)

                sql = f"""
                    SELECT id, project_id, dataset_code, parent_id, batch_id,
                           doc_no, entity_name, amount, currency, event_date,
                           status, payload, created_at, updated_at,
                           ts_rank_cd(tsv_content, plainto_tsquery('simple', ${q_idx})) AS score
                    FROM shared.records
                    WHERE {where_clause}
                    ORDER BY ts_rank_cd(tsv_content, plainto_tsquery('simple', ${q_idx})) DESC, event_date DESC, id DESC
                    LIMIT ${lim_idx} OFFSET ${off_idx};
                """
                rows = await conn.fetch(sql, *params)
                if rows:
                    results = []
                    for r in rows:
                        d = dict(r)
                        if isinstance(d.get("payload"), str):
                            d["payload"] = json.loads(d["payload"])
                        if "score" in d:
                            raw_score = float(d["score"])
                            # F-SEARCH-06: 强制单调归一化至 [0.0, 1.0) 契约域
                            d["score"] = round(raw_score / (1.0 + raw_score), 4)
                        if parsed_min_score is not None and not is_vector_fallback and d.get("score") is not None:
                            if d["score"] < parsed_min_score:
                                continue
                        results.append(d)
                    return (results, "fulltext_fallback") if return_mode else results

            # 若调用方显式提供了 query_vector 或 query_text，在未命中任何单据时安全返回空列表，绝不 Fallthrough 倾泻全表单据
            if query_vector or (query_text and query_text.strip()):
                mode = "semantic_vector" if (query_vector and has_vectors) else "fulltext_fallback"
                return ([], mode) if return_mode else []

            # 3. 仅在调用方未提供任何搜索词/向量时，作为常规浏览按时间倒序翻页
            params = list(base_params)
            where_clause = " AND ".join(base_conditions)
            params.append(limit)
            lim_idx = len(params)
            params.append(offset)
            off_idx = len(params)
            sql = f"""
                SELECT id, project_id, dataset_code, parent_id, batch_id,
                       doc_no, entity_name, amount, currency, event_date,
                       status, payload, created_at, updated_at
                FROM shared.records
                WHERE {where_clause}
                ORDER BY event_date DESC, id DESC
                LIMIT ${lim_idx} OFFSET ${off_idx};
            """
            rows = await conn.fetch(sql, *params)
            results = []
            for r in rows:
                d = dict(r)
                if isinstance(d.get("payload"), str):
                    d["payload"] = json.loads(d["payload"])
                results.append(d)
            return (results, "browse") if return_mode else results

    async def aggregate_record_stats(
        self,
        project_id: str,
        dataset_code: str | None = None,
        date_from: date | str | None = None,
        date_to: date | str | None = None,
        group_by: str = "month",
        status: str | list[str] | None = None,
    ) -> dict:
        """统计与汇总事实数据指标 (总笔数、总金额、分组汇总)。"""
        await self.ensure_datasets_and_records_schema()
        conditions = ["project_id = $1"]
        params: list[Any] = [project_id]

        if dataset_code:
            params.append(dataset_code.strip().lower())
            conditions.append(f"dataset_code = ${len(params)}")
        if date_from:
            d_from = date_from if isinstance(date_from, date) else date.fromisoformat(str(date_from).strip()[:10])
            params.append(d_from)
            conditions.append(f"event_date >= ${len(params)}")
        if date_to:
            d_to = date_to if isinstance(date_to, date) else date.fromisoformat(str(date_to).strip()[:10])
            params.append(d_to)
            conditions.append(f"event_date <= ${len(params)}")
        if status:
            if isinstance(status, (list, tuple, set)):
                st_arr = [str(s).strip().lower() for s in status if s and str(s).strip()]
                if st_arr:
                    params.append(st_arr)
                    conditions.append(f"status = ANY(${len(params)})")
            else:
                params.append(str(status).strip().lower())
                conditions.append(f"status = ${len(params)}")

        where_clause = " AND ".join(conditions)

        async with self._pool.acquire() as conn:
            summary_sql = f"""
                SELECT COUNT(*)::bigint AS total_count,
                       COALESCE(SUM(amount), 0) AS total_amount,
                       COALESCE(MIN(amount), 0) AS min_amount,
                       COALESCE(MAX(amount), 0) AS max_amount,
                       COALESCE(AVG(amount), 0) AS avg_amount
                FROM shared.records
                WHERE {where_clause};
            """
            sum_row = await conn.fetchrow(summary_sql, *params)
            summary = {
                "total_count": sum_row["total_count"],
                "total_amount": float(sum_row["total_amount"]),
                "min_amount": float(sum_row["min_amount"]),
                "max_amount": float(sum_row["max_amount"]),
                "avg_amount": float(sum_row["avg_amount"]),
            }

            group_by_norm = (group_by or "none").strip().lower()
            breakdown = []
            if group_by_norm in ("day", "month", "year", "currency", "status", "entity", "dataset"):
                if group_by_norm == "day":
                    group_expr = "event_date::text"
                elif group_by_norm == "month":
                    group_expr = "to_char(event_date, 'YYYY-MM')"
                elif group_by_norm == "year":
                    group_expr = "to_char(event_date, 'YYYY')"
                elif group_by_norm == "currency":
                    group_expr = "currency"
                elif group_by_norm == "status":
                    group_expr = "status"
                elif group_by_norm == "entity":
                    group_expr = "COALESCE(entity_name, 'Unknown')"
                elif group_by_norm == "dataset":
                    group_expr = "dataset_code"
                else:
                    group_expr = "to_char(event_date, 'YYYY-MM')"

                breakdown_sql = f"""
                    SELECT {group_expr} AS dimension,
                           COUNT(*)::bigint AS count,
                           COALESCE(SUM(amount), 0) AS total_amount,
                           COALESCE(AVG(amount), 0) AS avg_amount
                    FROM shared.records
                    WHERE {where_clause}
                    GROUP BY dimension
                    ORDER BY dimension ASC;
                """
                b_rows = await conn.fetch(breakdown_sql, *params)
                for r in b_rows:
                    breakdown.append({
                        "dimension": r["dimension"],
                        "count": r["count"],
                        "total_amount": float(r["total_amount"]),
                        "avg_amount": float(r["avg_amount"]),
                    })

            return {
                "project_id": project_id,
                "dataset_code": dataset_code,
                "summary": summary,
                "group_by": group_by_norm,
                "breakdown": breakdown,
            }


db = Database()


