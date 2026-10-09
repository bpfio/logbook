"""Logbook 异步数据库操作层 (asyncpg + PostgreSQL 18)

特性:
- 二进制协议直连，零外部 C 库依赖
- 连接池严格硬锁 (min_size=1, max_size=3, 符合 CPU*2 黄金法则)
- 双平面 Schema 隔离自动路由
- 向量 HNSW 距离与全文混合召回
"""

import os
import json
from typing import Any
import asyncpg
from .models import (
    Task, TaskStatus, TaskPriority, TaskType,
    Finding, FindingStatus, FindingSeverity,
    Waiting, WaitingStatus, WaitingCategory,
    Batch, BatchStatus,
    DevLog, DevLogVisibility,
    Rule,
    Project, slug_to_schema_name,
    AgentMessage, FileLease
)
from .time_sync import get_beijing_now


# LIMIT 统一钳制上限 (P1-4: 防 f-string 注入面 + 防 token 肥胖)
MAX_LIMIT = 200


class DevLogRecordResult(DevLog):
    """record_devlog 返回值: DevLog 全字段 + action 盖戳标记 (P0-1)。

    action: 'created' 全新入库 / 'updated' 四要素有变已更新 (调用方需重嵌入) /
            'cached' 内容未变 (向量缓存复用，勿重嵌入)。
    """
    action: str = "created"


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

    def __init__(self):
        db_url = os.getenv("DATABASE_URL")
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
        self._pool: asyncpg.Pool | None = None

    async def connect(self):
        import asyncio
        loop = asyncio.get_running_loop()
        if self._pool is not None and getattr(self._pool, "_loop", None) != loop:
            old_pool = self._pool
            self._pool = None
            try:
                old_pool.terminate()
            except Exception:
                pass

        if not self._pool:
            self._pool = await asyncpg.create_pool(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database,
                min_size=1,
                max_size=3,  # 严格控制在 3 个连接以内，杜绝多连接资源争抢
                command_timeout=10.0,
            )

    async def close(self):
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def ensure_project(self, project: str):
        """确保项目 Schema 已经初始化。"""
        await self.connect()
        async with self._pool.acquire() as conn:
            await conn.execute("SELECT shared.init_project_schema($1)", project)

    async def register_project(
        self,
        slug: str,
        title: str,
        description: str | None = None,
        schema_name: str | None = None
    ) -> Project:
        """注册新项目：初始化物理 Schema 并登记至 shared.projects 元数据中心。"""
        await self.connect()
        s_name = schema_name or slug_to_schema_name(slug)
        async with self._pool.acquire() as conn:
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
            row = await conn.fetchrow("""
                INSERT INTO shared.projects (slug, schema_name, title, description)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (slug) DO UPDATE
                SET schema_name = EXCLUDED.schema_name,
                    title = EXCLUDED.title,
                    description = COALESCE(EXCLUDED.description, shared.projects.description),
                    updated_at = CURRENT_TIMESTAMP
                RETURNING slug, schema_name, title, description, created_at, updated_at;
            """, slug, s_name, title, description)
            return Project(**dict(row))

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
    # 任务台账 (tasks)
    # =========================================================================

    async def upsert_task(self, project: str, task: Task, operator: str = "agy") -> Task:
        await self.ensure_project(project)
        # 获取原有状态以沉淀精确状态转移审计
        existing = await self.get_task(project, task.id)
        from_status = existing.status.value if existing else None

        query = f"""
            INSERT INTO "{project}".tasks (
                id, batch_id, parent_id, title, task_type, priority, status,
                assignee, commit_hash, proof_link, notes, tags, created_at, updated_at, started_at, closed_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                $13, $14, COALESCE($15, CASE WHEN $17 = 'closed' THEN NOW() END), $16)
            ON CONFLICT (id) DO UPDATE SET
                batch_id = EXCLUDED.batch_id,
                parent_id = EXCLUDED.parent_id,
                title = EXCLUDED.title,
                task_type = EXCLUDED.task_type,
                priority = EXCLUDED.priority,
                status = EXCLUDED.status,
                assignee = EXCLUDED.assignee,
                commit_hash = EXCLUDED.commit_hash,
                proof_link = EXCLUDED.proof_link,
                notes = EXCLUDED.notes,
                tags = EXCLUDED.tags,
                updated_at = EXCLUDED.updated_at,
                started_at = COALESCE(
                    "{project}".tasks.started_at,
                    EXCLUDED.started_at,
                    CASE WHEN EXCLUDED.status = 'closed' THEN NOW() END
                ),
                closed_at = COALESCE("{project}".tasks.closed_at, EXCLUDED.closed_at)
            RETURNING id, batch_id, parent_id, title, task_type, priority, status,
                      assignee, commit_hash, proof_link, notes, tags, created_at, updated_at,
                      started_at, closed_at, duration_seconds;
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                query,
                task.id, task.batch_id, task.parent_id, task.title, task.task_type.value, task.priority.value, task.status.value,
                task.assignee, task.commit_hash, task.proof_link, task.notes, task.tags, task.created_at, task.updated_at, task.started_at, task.closed_at,
                task.status.value
            )
            # 记录时间线流 (精确记录 from_status -> to_status)
            await conn.execute(
                f'INSERT INTO "{project}".task_timeline (task_id, from_status, to_status, operator, occurred_at) VALUES ($1, $2, $3, $4, $5)',
                task.id, from_status, task.status.value, operator, get_beijing_now()
            )
            return Task(**dict(row))

    async def query_tasks(
        self,
        project: str,
        status: list[TaskStatus] | None = None,
        priority: list[TaskPriority] | None = None,
        batch_id: str | None = None,
        assignee: str | None = None,
        parent_id: str | None = None,
        limit: int = 50
    ) -> list[Task]:
        await self.ensure_project(project)
        conditions = ["1=1"]
        params = []
        idx = 1

        if status:
            conditions.append(f"status = ANY(${idx})")
            params.append([s.value for s in status])
            idx += 1
        if priority:
            conditions.append(f"priority = ANY(${idx})")
            params.append([p.value for p in priority])
            idx += 1
        if batch_id:
            conditions.append(f"batch_id = ${idx}")
            params.append(batch_id)
            idx += 1
        if assignee:
            conditions.append(f"assignee = ${idx}")
            params.append(assignee)
            idx += 1
        if parent_id:
            conditions.append(f"parent_id = ${idx}")
            params.append(parent_id)
            idx += 1

        # P1-4: LIMIT 参数化 + 钳制上限，杜绝 f-string 注入面
        limit = self._clamp_limit(limit)
        params.append(limit)
        query = f"""
            SELECT id, batch_id, parent_id, title, task_type, priority, status,
                   assignee, commit_hash, proof_link, notes, tags, created_at, updated_at,
                   started_at, closed_at, duration_seconds
            FROM "{project}".tasks
            WHERE {" AND ".join(conditions)}
            ORDER BY created_at DESC
            LIMIT ${idx};
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [Task(**dict(r)) for r in rows]

    async def get_task(self, project: str, task_id: str) -> Task | None:
        await self.ensure_project(project)
        query = f"""
            SELECT id, batch_id, parent_id, title, task_type, priority, status,
                   assignee, commit_hash, proof_link, notes, tags, created_at, updated_at,
                   started_at, closed_at, duration_seconds
            FROM "{project}".tasks
            WHERE id = $1;
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, task_id)
            return Task(**dict(row)) if row else None

    async def bulk_upsert_tasks(self, project: str, tasks: list[Task]) -> list[Task]:
        """在单个事务中原子批量插入或更新多条任务。"""
        await self.ensure_project(project)
        if not tasks:
            return []
        query = f"""
            INSERT INTO "{project}".tasks (
                id, batch_id, parent_id, title, task_type, priority, status,
                assignee, commit_hash, proof_link, notes, tags,
                created_at, updated_at, started_at, closed_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12,
                $13, $14, COALESCE($15, CASE WHEN $17 = 'closed' THEN NOW() END), $16
            )
            ON CONFLICT (id) DO UPDATE SET
                batch_id = COALESCE(EXCLUDED.batch_id, "{project}".tasks.batch_id),
                parent_id = COALESCE(EXCLUDED.parent_id, "{project}".tasks.parent_id),
                title = EXCLUDED.title,
                task_type = EXCLUDED.task_type,
                priority = EXCLUDED.priority,
                status = EXCLUDED.status,
                assignee = EXCLUDED.assignee,
                commit_hash = COALESCE(EXCLUDED.commit_hash, "{project}".tasks.commit_hash),
                proof_link = COALESCE(EXCLUDED.proof_link, "{project}".tasks.proof_link),
                notes = COALESCE(EXCLUDED.notes, "{project}".tasks.notes),
                tags = EXCLUDED.tags,
                updated_at = EXCLUDED.updated_at,
                started_at = COALESCE(
                    "{project}".tasks.started_at,
                    EXCLUDED.started_at,
                    CASE WHEN EXCLUDED.status = 'closed' THEN NOW() END
                ),
                closed_at = COALESCE("{project}".tasks.closed_at, EXCLUDED.closed_at)
            RETURNING id, batch_id, parent_id, title, task_type, priority, status,
                      assignee, commit_hash, proof_link, notes, tags, created_at, updated_at,
                      started_at, closed_at, duration_seconds;
        """
        results = []
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for t in tasks:
                    row = await conn.fetchrow(
                        query,
                        t.id, t.batch_id, t.parent_id, t.title, t.task_type.value, t.priority.value, t.status.value,
                        t.assignee, t.commit_hash, t.proof_link, t.notes, t.tags,
                        t.created_at, t.updated_at, t.started_at, t.closed_at,
                        t.status.value
                    )
                    results.append(Task(**dict(row)))
        return results

    # =========================================================================
    # 发现与缺陷台账 (findings)
    # =========================================================================

    async def upsert_finding(self, project: str, finding: Finding) -> Finding:
        await self.ensure_project(project)
        query = f"""
            INSERT INTO "{project}".findings (
                id, source, severity, status, task_id, reporter, summary, resolution, discovered_at, resolved_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (id) DO UPDATE SET
                source = EXCLUDED.source,
                severity = EXCLUDED.severity,
                status = EXCLUDED.status,
                task_id = EXCLUDED.task_id,
                reporter = EXCLUDED.reporter,
                summary = EXCLUDED.summary,
                resolution = EXCLUDED.resolution,
                resolved_at = CASE
                    WHEN EXCLUDED.status = 'fixed'
                        THEN COALESCE("{project}".findings.resolved_at, EXCLUDED.resolved_at, NOW())
                    ELSE NULL
                END
            RETURNING id, source, severity, status, task_id, reporter, summary, resolution, discovered_at, resolved_at;
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                query,
                finding.id, finding.source, finding.severity.value, finding.status.value,
                finding.task_id, finding.reporter, finding.summary, finding.resolution,
                finding.discovered_at, finding.resolved_at
            )
            return Finding(**dict(row))

    async def query_findings(
        self,
        project: str,
        status: list[FindingStatus] | None = None,
        severity: list[FindingSeverity] | None = None,
        limit: int = 50
    ) -> list[Finding]:
        await self.ensure_project(project)
        conditions = ["1=1"]
        params = []
        idx = 1

        if status:
            conditions.append(f"status = ANY(${idx})")
            params.append([s.value for s in status])
            idx += 1
        if severity:
            conditions.append(f"severity = ANY(${idx})")
            params.append([p.value for p in severity])
            idx += 1

        limit = self._clamp_limit(limit)
        params.append(limit)
        query = f"""
            SELECT id, source, severity, status, task_id, reporter, summary, resolution, discovered_at, resolved_at
            FROM "{project}".findings
            WHERE {" AND ".join(conditions)}
            ORDER BY discovered_at DESC
            LIMIT ${idx};
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [Finding(**dict(r)) for r in rows]

    async def bulk_upsert_findings(self, project: str, items: list[Finding]) -> list[Finding]:
        """在单个事务中原子批量插入或更新多条发现 (P1-3，盖戳语义与 upsert_finding 一致)。"""
        await self.ensure_project(project)
        if not items:
            return []
        query = f"""
            INSERT INTO "{project}".findings (
                id, source, severity, status, task_id, reporter, summary, resolution, discovered_at, resolved_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (id) DO UPDATE SET
                source = EXCLUDED.source,
                severity = EXCLUDED.severity,
                status = EXCLUDED.status,
                task_id = EXCLUDED.task_id,
                reporter = EXCLUDED.reporter,
                summary = EXCLUDED.summary,
                resolution = EXCLUDED.resolution,
                resolved_at = CASE
                    WHEN EXCLUDED.status = 'fixed'
                        THEN COALESCE("{project}".findings.resolved_at, EXCLUDED.resolved_at, NOW())
                    ELSE NULL
                END
            RETURNING id, source, severity, status, task_id, reporter, summary, resolution, discovered_at, resolved_at;
        """
        results = []
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for f in items:
                    row = await conn.fetchrow(
                        query,
                        f.id, f.source, f.severity.value, f.status.value,
                        f.task_id, f.reporter, f.summary, f.resolution,
                        f.discovered_at, f.resolved_at
                    )
                    results.append(Finding(**dict(row)))
        return results

    # =========================================================================
    # 待办与阻塞台账 (waitings)
    # =========================================================================

    async def upsert_waiting(self, project: str, waiting: Waiting) -> Waiting:
        await self.ensure_project(project)
        query = f"""
            INSERT INTO "{project}".waitings (
                id, category, status, owner, description, resolution, blocked_at, resolved_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (id) DO UPDATE SET
                category = EXCLUDED.category,
                status = EXCLUDED.status,
                owner = EXCLUDED.owner,
                description = EXCLUDED.description,
                resolution = EXCLUDED.resolution,
                resolved_at = CASE
                    WHEN EXCLUDED.status = 'closed'
                        THEN COALESCE("{project}".waitings.resolved_at, EXCLUDED.resolved_at, NOW())
                    ELSE NULL
                END
            RETURNING id, category, status, owner, description, resolution, blocked_at, resolved_at;
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                query,
                waiting.id, waiting.category.value, waiting.status.value, waiting.owner,
                waiting.description, waiting.resolution, waiting.blocked_at, waiting.resolved_at
            )
            return Waiting(**dict(row))

    async def query_waitings(
        self,
        project: str,
        status: WaitingStatus | None = None,
        limit: int = 50
    ) -> list[Waiting]:
        await self.ensure_project(project)
        conditions = ["1=1"]
        params = []
        idx = 1
        if status:
            conditions.append(f"status = ${idx}")
            params.append(status.value)
            idx += 1

        limit = self._clamp_limit(limit)
        params.append(limit)
        query = f"""
            SELECT id, category, status, owner, description, resolution, blocked_at, resolved_at
            FROM "{project}".waitings
            WHERE {" AND ".join(conditions)}
            ORDER BY blocked_at DESC
            LIMIT ${idx};
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [Waiting(**dict(r)) for r in rows]

    async def bulk_upsert_waitings(self, project: str, items: list[Waiting]) -> list[Waiting]:
        """在单个事务中原子批量插入或更新多条待办 (P1-3，盖戳语义与 upsert_waiting 一致)。"""
        await self.ensure_project(project)
        if not items:
            return []
        query = f"""
            INSERT INTO "{project}".waitings (
                id, category, status, owner, description, resolution, blocked_at, resolved_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (id) DO UPDATE SET
                category = EXCLUDED.category,
                status = EXCLUDED.status,
                owner = EXCLUDED.owner,
                description = EXCLUDED.description,
                resolution = EXCLUDED.resolution,
                resolved_at = CASE
                    WHEN EXCLUDED.status = 'closed'
                        THEN COALESCE("{project}".waitings.resolved_at, EXCLUDED.resolved_at, NOW())
                    ELSE NULL
                END
            RETURNING id, category, status, owner, description, resolution, blocked_at, resolved_at;
        """
        results = []
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                for w in items:
                    row = await conn.fetchrow(
                        query,
                        w.id, w.category.value, w.status.value, w.owner,
                        w.description, w.resolution, w.blocked_at, w.resolved_at
                    )
                    results.append(Waiting(**dict(row)))
        return results

    # =========================================================================
    # 研发批次 (batches)
    # =========================================================================

    async def upsert_batch(self, project: str, batch: Batch) -> Batch:
        await self.ensure_project(project)
        query = f"""
            INSERT INTO "{project}".batches (
                id, title, status, branch_name, summary, methodology_notes, created_at, closed_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            ON CONFLICT (id) DO UPDATE SET
                title = EXCLUDED.title,
                status = EXCLUDED.status,
                branch_name = EXCLUDED.branch_name,
                summary = EXCLUDED.summary,
                methodology_notes = EXCLUDED.methodology_notes,
                closed_at = EXCLUDED.closed_at
            RETURNING id, title, status, branch_name, summary, methodology_notes, created_at, closed_at;
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                query,
                batch.id, batch.title, batch.status.value, batch.branch_name,
                batch.summary, batch.methodology_notes, batch.created_at, batch.closed_at
            )
            return Batch(**dict(row))

    async def query_batches(self, project: str, limit: int = 20) -> list[Batch]:
        await self.ensure_project(project)
        limit = self._clamp_limit(limit, default=20)
        query = f"""
            SELECT id, title, status, branch_name, summary, methodology_notes, created_at, closed_at
            FROM "{project}".batches
            ORDER BY created_at DESC
            LIMIT {limit};
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query)
            return [Batch(**dict(r)) for r in rows]

    # =========================================================================
    # 共享知识库：排查手记 (shared.devlogs)
    # =========================================================================

    async def record_devlog(self, devlog: DevLog) -> DevLogRecordResult:
        """入库排障手记 (P0-1: ON CONFLICT upsert，幂等键 = uq_devlogs_proj_task_title)。

        幂等键: (project_id, COALESCE(task_id,''), title)，task_id 允许 NULL。
        返回 DevLogRecordResult (DevLog + action 字段):
          - action='created': 全新手记入库
          - action='updated': 同键手记四要素有变，已更新内容 (调用方需重嵌入)
          - action='cached':  同键手记内容未变，向量缓存复用 (勿重嵌入)
        """
        await self.connect()
        vec_literal = None
        if devlog.embedding:
            vec_literal = f"[{','.join(str(x) for x in devlog.embedding)}]"

        query = """
            INSERT INTO shared.devlogs (
                project_id, task_id, title, author, problem, root_cause, solution, evidence,
                visibility, tags, embedding, occurred_at, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::vector, $12, $13, $14)
            ON CONFLICT (project_id, COALESCE(task_id, ''), title) DO UPDATE SET
                author = EXCLUDED.author,
                problem = EXCLUDED.problem,
                root_cause = EXCLUDED.root_cause,
                solution = EXCLUDED.solution,
                evidence = EXCLUDED.evidence,
                visibility = EXCLUDED.visibility,
                tags = EXCLUDED.tags,
                embedding = COALESCE(EXCLUDED.embedding, shared.devlogs.embedding),
                updated_at = NOW()
            RETURNING id, project_id, task_id, title, author, problem, root_cause, solution, evidence,
                      visibility, tags, occurred_at, created_at, updated_at;
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                # 先取旧四要素判定内容是否变化 (RETURNING 拿不到更新前值)
                old = await conn.fetchrow(
                    """SELECT problem, root_cause, solution, evidence, author FROM shared.devlogs
                       WHERE project_id = $1 AND COALESCE(task_id, '') = COALESCE($2, '') AND title = $3
                       FOR UPDATE;""",
                    devlog.project_id, devlog.task_id, devlog.title
                )
                row = await conn.fetchrow(
                    query,
                    devlog.project_id, devlog.task_id, devlog.title, devlog.author,
                    devlog.problem, devlog.root_cause, devlog.solution, devlog.evidence,
                    devlog.visibility.value, devlog.tags, vec_literal,
                    devlog.occurred_at, devlog.created_at, devlog.updated_at
                )
            res = dict(row)
            res["embedding"] = devlog.embedding
            if old is None:
                action = "created"
            elif (old["problem"], old["root_cause"], old["solution"], old["evidence"]) == \
                 (devlog.problem, devlog.root_cause, devlog.solution, devlog.evidence):
                action = "cached"
            else:
                action = "updated"
            return DevLogRecordResult(**res, action=action)

    async def find_existing_devlog(
        self,
        project_id: str,
        devlog_id: int | None = None,
        task_id: str | None = None,
        title: str | None = None
    ) -> dict | None:
        """根据 id / (project, task_id) / (project, title) 查找现有排障手记。"""
        await self.connect()
        async with self._pool.acquire() as conn:
            if devlog_id:
                row = await conn.fetchrow("SELECT * FROM shared.devlogs WHERE id = $1;", devlog_id)
                if row:
                    return dict(row)
            if task_id:
                row = await conn.fetchrow(
                    "SELECT * FROM shared.devlogs WHERE project_id = $1 AND task_id = $2 ORDER BY id DESC LIMIT 1;",
                    project_id, task_id
                )
                if row:
                    return dict(row)
            if title:
                row = await conn.fetchrow(
                    "SELECT * FROM shared.devlogs WHERE project_id = $1 AND title = $2 ORDER BY id DESC LIMIT 1;",
                    project_id, title
                )
                if row:
                    return dict(row)
            return None

    async def update_devlog(
        self,
        devlog_id: int,
        title: str,
        author: str,
        problem: str,
        root_cause: str,
        solution: str,
        evidence: str,
        visibility: str,
        tags: list[str],
        embedding: list[float] | None = None,
        task_id: str | None = None,
    ) -> dict:
        """更新已有手记。若 embedding 为 None，则保留原有向量不覆盖。"""
        await self.connect()
        async with self._pool.acquire() as conn:
            if embedding is not None:
                vec_literal = f"[{','.join(str(x) for x in embedding)}]"
                sql = """
                    UPDATE shared.devlogs
                    SET title = $2, author = $3, problem = $4, root_cause = $5,
                        solution = $6, evidence = $7, visibility = $8, tags = $9,
                        task_id = $10, embedding = $11::vector, updated_at = NOW()
                    WHERE id = $1
                    RETURNING id, project_id, task_id, title, author, problem, root_cause, solution, evidence,
                              visibility, tags, occurred_at, created_at, updated_at;
                """
                row = await conn.fetchrow(
                    sql, devlog_id, title, author, problem, root_cause,
                    solution, evidence, visibility, tags, task_id, vec_literal
                )
            else:
                sql = """
                    UPDATE shared.devlogs
                    SET title = $2, author = $3, problem = $4, root_cause = $5,
                        solution = $6, evidence = $7, visibility = $8, tags = $9,
                        task_id = $10, updated_at = NOW()
                    WHERE id = $1
                    RETURNING id, project_id, task_id, title, author, problem, root_cause, solution, evidence,
                              visibility, tags, occurred_at, created_at, updated_at;
                """
                row = await conn.fetchrow(
                    sql, devlog_id, title, author, problem, root_cause,
                    solution, evidence, visibility, tags, task_id
                )
            return dict(row)

    async def search_devlogs(
        self,
        project: str,
        query_vector: list[float] | None = None,
        query_text: str | None = None,
        limit: int = 5
    ) -> list[dict]:
        await self.connect()
        # P0-4: 超采后按 (task_id, title) 分组取 top-1，兜底吸收重复副本
        limit = self._clamp_limit(limit, default=5)
        fetch_limit = min(limit * 3, MAX_LIMIT)
        async with self._pool.acquire() as conn:
            # 严格密级防线：只能查本项目手记 或 明确为 public_safe 的手记
            vis_cond = "(project_id = $1 OR visibility = 'public_safe')"
            
            if query_vector:
                vec_literal = f"[{','.join(str(x) for x in query_vector)}]"
                sql = f"""
                    SELECT id, project_id, task_id, title, author, problem, root_cause, solution, evidence,
                           visibility, tags, occurred_at, created_at, updated_at,
                           (1 - (embedding <=> $2::vector)) AS score
                    FROM shared.devlogs
                    WHERE {vis_cond} AND embedding IS NOT NULL
                    ORDER BY embedding <=> $2::vector
                    LIMIT $3;
                """
                rows = await conn.fetch(sql, project, vec_literal, fetch_limit)
            elif query_text:
                sql = f"""
                    SELECT id, project_id, task_id, title, author, problem, root_cause, solution, evidence,
                           visibility, tags, occurred_at, created_at, updated_at,
                           ts_rank_cd(tsv_content, plainto_tsquery('simple', $2)) AS score
                    FROM shared.devlogs
                    WHERE {vis_cond} AND tsv_content @@ plainto_tsquery('simple', $2)
                    ORDER BY score DESC
                    LIMIT $3;
                """
                rows = await conn.fetch(sql, project, query_text, fetch_limit)
            else:
                sql = f"""
                    SELECT id, project_id, task_id, title, author, problem, root_cause, solution, evidence,
                           visibility, tags, occurred_at, created_at, updated_at, 1.0 AS score
                    FROM shared.devlogs
                    WHERE {vis_cond}
                    ORDER BY occurred_at DESC
                    LIMIT $2;
                """
                rows = await conn.fetch(sql, project, fetch_limit)

            # P0-4 去重兜底: 同 (task_id, title) 分组仅保留得分最高副本
            seen: dict[tuple, dict] = {}
            for r in rows:
                d = dict(r)
                key = (d.get("task_id"), d.get("title"))
                if key not in seen or (d.get("score") or 0) > (seen[key].get("score") or 0):
                    seen[key] = d
            return list(seen.values())[:limit]

    # =========================================================================
    # P1-1: 项目简报 digest (供 mcp brief 工具消费，单包 ≤2K token)
    # =========================================================================

    async def brief_project(self, project_id: str, devlog_limit: int = 5) -> dict:
        """一次查询拼装项目全貌简报。

        返回 dict，键固定 (跨线接口契约，不得更改):
          - tasks_open:     未结任务 (planned/running/blocked) 精简列 list[dict]
          - findings_open:  未闭环发现 (open/infix/blocked) 精简列 list[dict]
          - waitings_open:  待办/阻塞中 (open) 精简列 list[dict]
          - batch_running:  running 批次 list[dict]
          - recent_devlogs: 最近 N 条手记 (id/task_id/title/updated_at) list[dict]
        """
        await self.ensure_project(project_id)
        n = self._clamp_limit(devlog_limit, default=5)
        async with self._pool.acquire() as conn:
            tasks_open = await conn.fetch(f"""
                SELECT id, title, status, priority, assignee, updated_at
                FROM "{project_id}".tasks
                WHERE status IN ('planned', 'running', 'blocked')
                ORDER BY updated_at DESC;
            """)
            findings_open = await conn.fetch(f"""
                SELECT id, severity, status, task_id, summary
                FROM "{project_id}".findings
                WHERE status IN ('open', 'infix', 'blocked')
                ORDER BY discovered_at DESC;
            """)
            waitings_open = await conn.fetch(f"""
                SELECT id, category, owner, description
                FROM "{project_id}".waitings
                WHERE status = 'open'
                ORDER BY blocked_at DESC;
            """)
            batch_running = await conn.fetch(f"""
                SELECT id, title FROM "{project_id}".batches WHERE status = 'running'
                ORDER BY created_at DESC;
            """)
            recent_devlogs = await conn.fetch(
                """SELECT id, task_id, title, updated_at
                   FROM shared.devlogs
                   WHERE project_id = $1
                   ORDER BY updated_at DESC
                   LIMIT $2;""",
                project_id, n
            )
        return {
            "tasks_open": [dict(r) for r in tasks_open],
            "findings_open": [dict(r) for r in findings_open],
            "waitings_open": [dict(r) for r in waitings_open],
            "batch_running": [dict(r) for r in batch_running],
            "recent_devlogs": [dict(r) for r in recent_devlogs],
        }

    # =========================================================================
    # 共享知识库：架构铁律 (shared.rules)
    # =========================================================================

    async def upsert_rule(self, rule: Rule) -> Rule:
        await self.connect()
        query = """
            INSERT INTO shared.rules (
                id, category, title, summary, bad_practice, good_practice, constraints, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (id) DO UPDATE SET
                category = EXCLUDED.category,
                title = EXCLUDED.title,
                summary = EXCLUDED.summary,
                bad_practice = EXCLUDED.bad_practice,
                good_practice = EXCLUDED.good_practice,
                constraints = EXCLUDED.constraints,
                updated_at = EXCLUDED.updated_at
            RETURNING id, category, title, summary, bad_practice, good_practice, constraints, created_at, updated_at;
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                query,
                rule.id, rule.category, rule.title, rule.summary, rule.bad_practice,
                rule.good_practice, rule.constraints, rule.created_at, rule.updated_at
            )
            return Rule(**dict(row))

    async def query_rules(self, keyword: str | None = None, category: str | None = None) -> list[Rule]:
        await self.connect()
        async with self._pool.acquire() as conn:
            conditions = ["1=1"]
            params = []
            idx = 1
            if category:
                conditions.append(f"category = ${idx}")
                params.append(category)
                idx += 1
            if keyword:
                conditions.append(f"(title ILIKE ${idx} OR summary ILIKE ${idx} OR constraints ILIKE ${idx})")
                params.append(f"%{keyword}%")
                idx += 1

            query = f"""
                SELECT id, category, title, summary, bad_practice, good_practice, constraints, created_at, updated_at
                FROM shared.rules
                WHERE {" AND ".join(conditions)}
                ORDER BY id;
            """
            rows = await conn.fetch(query, *params)
            return [Rule(**dict(r)) for r in rows]

    # =========================================================================
    # 多 Agent 对讲信箱与代码文件租约 (0.3.0)
    # =========================================================================

    async def ensure_messaging_schema(self):
        """确保 shared.agent_messages 与 shared.file_leases 物理表存在 (幂等自愈)。"""
        await self.connect()
        async with self._pool.acquire() as conn:
            await conn.execute("""
                CREATE TABLE IF NOT EXISTS shared.agent_messages (
                    id BIGSERIAL PRIMARY KEY,
                    project_id VARCHAR(32) NOT NULL,
                    from_agent VARCHAR(64) NOT NULL,
                    from_ip VARCHAR(45) NOT NULL DEFAULT '0.0.0.0',
                    to_agent VARCHAR(64) NOT NULL,
                    to_ip VARCHAR(45) NOT NULL DEFAULT '0.0.0.0',
                    subject VARCHAR(256) NOT NULL,
                    content TEXT NOT NULL,
                    task_id VARCHAR(32),
                    thread_id VARCHAR(64),
                    is_read BOOLEAN NOT NULL DEFAULT FALSE,
                    read_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                ALTER TABLE shared.agent_messages ADD COLUMN IF NOT EXISTS from_ip VARCHAR(45) NOT NULL DEFAULT '0.0.0.0';
                ALTER TABLE shared.agent_messages ADD COLUMN IF NOT EXISTS to_ip VARCHAR(45) NOT NULL DEFAULT '0.0.0.0';

                CREATE INDEX IF NOT EXISTS idx_agent_messages_inbox
                    ON shared.agent_messages (project_id, to_agent, is_read, created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_agent_messages_task
                    ON shared.agent_messages (project_id, task_id);
                CREATE INDEX IF NOT EXISTS idx_agent_messages_thread
                    ON shared.agent_messages (project_id, thread_id);

                CREATE TABLE IF NOT EXISTS shared.file_leases (
                    id BIGSERIAL PRIMARY KEY,
                    project_id VARCHAR(32) NOT NULL,
                    agent_name VARCHAR(64) NOT NULL,
                    file_path VARCHAR(512) NOT NULL,
                    lease_expires_at TIMESTAMPTZ NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    CONSTRAINT uq_project_file_lease UNIQUE (project_id, file_path)
                );
                CREATE INDEX IF NOT EXISTS idx_file_leases_lookup
                    ON shared.file_leases (project_id, lease_expires_at);
            """)

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
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO shared.agent_messages (
                    project_id, from_agent, from_ip, to_agent, to_ip, subject, content, task_id, thread_id
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                RETURNING id, project_id, from_agent, from_ip, to_agent, to_ip, subject, content,
                          task_id, thread_id, is_read, read_at, created_at;
            """, project_id, from_agent, from_ip, to_agent, to_ip, subject, content, task_id, thread_id)
            return AgentMessage(**dict(row))

    async def get_agent_inbox(
        self,
        project_id: str | None,
        agent_name: str,
        unread_only: bool = True,
        limit: int = 20,
    ) -> list[AgentMessage]:
        await self.ensure_messaging_schema()
        limit = self._clamp_limit(limit, default=20)
        conditions = ["to_agent = $1"]
        params: list[Any] = [agent_name]

        if unread_only:
            conditions.append("is_read = FALSE")
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

    async def read_agent_message(
        self,
        message_id: int,
        agent_name: str | None = None,
    ) -> AgentMessage | None:
        await self.ensure_messaging_schema()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("""
                UPDATE shared.agent_messages
                SET is_read = TRUE,
                    read_at = COALESCE(read_at, CURRENT_TIMESTAMP)
                WHERE id = $1
                RETURNING id, project_id, from_agent, from_ip, to_agent, to_ip, subject, content,
                          task_id, thread_id, is_read, read_at, created_at;
            """, message_id)
            if not row:
                return None
            return AgentMessage(**dict(row))

    async def acquire_file_lease(
        self,
        project_id: str,
        agent_name: str,
        file_path: str,
        duration_seconds: int = 300,
    ) -> tuple[bool, FileLease | None, str | None]:
        """申请代码文件租约。返回 (success, lease, conflict_agent_if_failed)。"""
        await self.ensure_messaging_schema()
        duration_seconds = max(10, min(duration_seconds, 3600))
        async with self._pool.acquire() as conn:
            existing = await conn.fetchrow("""
                SELECT id, project_id, agent_name, file_path, lease_expires_at, created_at
                FROM shared.file_leases
                WHERE project_id = $1 AND file_path = $2;
            """, project_id, file_path)

            now = get_beijing_now()
            if existing:
                exp = existing["lease_expires_at"]
                if exp > now and existing["agent_name"] != agent_name:
                    return False, FileLease(**dict(existing)), existing["agent_name"]

            row = await conn.fetchrow("""
                INSERT INTO shared.file_leases (project_id, agent_name, file_path, lease_expires_at, created_at)
                VALUES ($1, $2, $3, CURRENT_TIMESTAMP + ($4 || ' seconds')::INTERVAL, CURRENT_TIMESTAMP)
                ON CONFLICT (project_id, file_path) DO UPDATE SET
                    agent_name = EXCLUDED.agent_name,
                    lease_expires_at = CURRENT_TIMESTAMP + ($4 || ' seconds')::INTERVAL,
                    created_at = CURRENT_TIMESTAMP
                RETURNING id, project_id, agent_name, file_path, lease_expires_at, created_at;
            """, project_id, agent_name, file_path, str(duration_seconds))
            return True, FileLease(**dict(row)), None

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
                SELECT id, project_id, agent_name, file_path, lease_expires_at, created_at
                FROM shared.file_leases
                WHERE project_id = $1 AND lease_expires_at > CURRENT_TIMESTAMP
                ORDER BY lease_expires_at ASC;
            """, project_id)
            return [FileLease(**dict(r)) for r in rows]


db = Database()
