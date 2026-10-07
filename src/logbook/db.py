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
    Rule
)
from .time_sync import get_beijing_now


class Database:
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
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
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
                started_at = COALESCE("{project}".tasks.started_at, EXCLUDED.started_at),
                closed_at = EXCLUDED.closed_at
            RETURNING id, batch_id, parent_id, title, task_type, priority, status,
                      assignee, commit_hash, proof_link, notes, tags, created_at, updated_at,
                      started_at, closed_at, duration_seconds;
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                query,
                task.id, task.batch_id, task.parent_id, task.title, task.task_type.value, task.priority.value, task.status.value,
                task.assignee, task.commit_hash, task.proof_link, task.notes, task.tags, task.created_at, task.updated_at, task.started_at, task.closed_at
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

        query = f"""
            SELECT id, batch_id, parent_id, title, task_type, priority, status,
                   assignee, commit_hash, proof_link, notes, tags, created_at, updated_at,
                   started_at, closed_at, duration_seconds
            FROM "{project}".tasks
            WHERE {" AND ".join(conditions)}
            ORDER BY created_at DESC
            LIMIT {limit};
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
                resolved_at = EXCLUDED.resolved_at
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

        query = f"""
            SELECT id, source, severity, status, task_id, reporter, summary, resolution, discovered_at, resolved_at
            FROM "{project}".findings
            WHERE {" AND ".join(conditions)}
            ORDER BY discovered_at DESC
            LIMIT {limit};
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [Finding(**dict(r)) for r in rows]

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
                resolved_at = EXCLUDED.resolved_at
            RETURNING id, category, status, owner, description, resolution, blocked_at, resolved_at;
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                query,
                waiting.id, waiting.category.value, waiting.status.value, waiting.owner,
                waiting.description, waiting.resolution, waiting.blocked_at, waiting.resolved_at
            )
            return Waiting(**dict(row))

    async def query_waitings(self, project: str, status: WaitingStatus | None = None) -> list[Waiting]:
        await self.ensure_project(project)
        conditions = ["1=1"]
        params = []
        if status:
            conditions.append("status = $1")
            params.append(status.value)

        query = f"""
            SELECT id, category, status, owner, description, resolution, blocked_at, resolved_at
            FROM "{project}".waitings
            WHERE {" AND ".join(conditions)}
            ORDER BY blocked_at DESC;
        """
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [Waiting(**dict(r)) for r in rows]

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

    async def record_devlog(self, devlog: DevLog) -> DevLog:
        await self.connect()
        # 向量值转换
        vec_literal = None
        if devlog.embedding:
            vec_literal = f"[{','.join(str(x) for x in devlog.embedding)}]"

        query = """
            INSERT INTO shared.devlogs (
                project_id, task_id, title, author, problem, root_cause, solution, evidence,
                visibility, tags, embedding, occurred_at, created_at, updated_at
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::vector, $12, $13, $14)
            RETURNING id, project_id, task_id, title, author, problem, root_cause, solution, evidence,
                      visibility, tags, occurred_at, created_at, updated_at;
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                query,
                devlog.project_id, devlog.task_id, devlog.title, devlog.author,
                devlog.problem, devlog.root_cause, devlog.solution, devlog.evidence,
                devlog.visibility.value, devlog.tags, vec_literal,
                devlog.occurred_at, devlog.created_at, devlog.updated_at
            )
            res = dict(row)
            res["embedding"] = devlog.embedding
            return DevLog(**res)

    async def search_devlogs(
        self,
        project: str,
        query_vector: list[float] | None = None,
        query_text: str | None = None,
        limit: int = 5
    ) -> list[dict]:
        await self.connect()
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
                rows = await conn.fetch(sql, project, vec_literal, limit)
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
                rows = await conn.fetch(sql, project, query_text, limit)
            else:
                sql = f"""
                    SELECT id, project_id, task_id, title, author, problem, root_cause, solution, evidence,
                           visibility, tags, occurred_at, created_at, updated_at, 1.0 AS score
                    FROM shared.devlogs
                    WHERE {vis_cond}
                    ORDER BY occurred_at DESC
                    LIMIT $2;
                """
                rows = await conn.fetch(sql, project, limit)

            return [dict(r) for r in rows]

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


db = Database()
