"""Versioned PostgreSQL migration runner for Logbook."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path

import asyncpg

from logbook.db import Database

MIGRATION_FILE_RE = re.compile(r"^(?P<version>\d{2})_[A-Za-z0-9_]+\.sql$")
ADVISORY_LOCK_ID = 0x6C6F67626F6F6B
MIGRATION_TABLE = "public.schema_migrations"


@dataclass(frozen=True)
class Migration:
    version: str
    path: Path
    checksum: str

    @property
    def name(self) -> str:
        return self.path.name


def migrations_dir() -> Path:
    configured = os.getenv("LOGBOOK_MIGRATIONS_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[2] / "sql"


def discover_migrations(directory: Path | None = None) -> list[Migration]:
    root = directory or migrations_dir()
    if not root.is_dir():
        raise FileNotFoundError(f"migration directory does not exist: {root}")

    migrations: list[Migration] = []
    seen: set[str] = set()
    for path in sorted(root.glob("*.sql")):
        match = MIGRATION_FILE_RE.match(path.name)
        if not match:
            continue
        version = match.group("version")
        if version in seen:
            raise ValueError(f"duplicate migration version {version}: {path}")
        seen.add(version)
        content = path.read_bytes()
        migrations.append(
            Migration(
                version=version,
                path=path,
                checksum=hashlib.sha256(content).hexdigest(),
            )
        )

    expected = [f"{index:02d}" for index in range(1, len(migrations) + 1)]
    actual = [migration.version for migration in migrations]
    if actual != expected:
        raise ValueError(
            f"migration versions must be contiguous from 01: got {', '.join(actual) or 'none'}"
        )
    if not migrations:
        raise ValueError(f"no versioned migrations found in {root}")
    return migrations


async def _ensure_migration_table(conn: asyncpg.Connection) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS public.schema_migrations (
            version TEXT PRIMARY KEY,
            checksum TEXT NOT NULL,
            source TEXT NOT NULL DEFAULT 'runner',
            applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            duration_ms INTEGER NOT NULL DEFAULT 0
        )
        """
    )


async def _detect_existing_versions(conn: asyncpg.Connection) -> list[str]:
    checks = {
        "01": """
            SELECT to_regprocedure('shared.init_project_schema(text)') IS NOT NULL
        """,
        "02": """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'shared' AND table_name = 'rules'
                  AND column_name = 'category'
            )
        """,
        "03": """
            SELECT EXISTS (
                SELECT 1 FROM pg_indexes
                WHERE schemaname = 'shared'
                  AND indexname = 'uq_devlogs_proj_task_title'
            )
        """,
        "04": """
            SELECT to_regclass('shared.projects') IS NOT NULL
        """,
        "05": """
            SELECT to_regclass('shared.agent_messages') IS NOT NULL
        """,
        "06": """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.columns
                WHERE table_schema = 'shared' AND table_name = 'devlogs'
                  AND column_name = 'agent_ip'
            )
        """,
        "07": """
            SELECT to_regclass('shared.researches') IS NOT NULL
        """,
    }
    detected: list[str] = []
    for version, query in checks.items():
        if bool(await conn.fetchval(query)):
            detected.append(version)
    return detected


async def _adopt_existing_schema(
    conn: asyncpg.Connection,
    migrations: list[Migration],
) -> list[str]:
    detected = await _detect_existing_versions(conn)
    if not detected:
        return []

    known = {migration.version for migration in migrations}
    unknown = sorted(set(detected) - known)
    if unknown:
        raise RuntimeError(f"database contains unknown migration versions: {', '.join(unknown)}")

    highest = max(detected)
    expected_prefix = [migration.version for migration in migrations if migration.version <= highest]
    if detected != expected_prefix:
        missing = [version for version in expected_prefix if version not in detected]
        raise RuntimeError(
            "existing schema has a migration gap; manual reconciliation required; "
            f"missing detected versions: {', '.join(missing)}"
        )

    by_version = {migration.version: migration for migration in migrations}
    for version in detected:
        migration = by_version[version]
        await conn.execute(
            """
            INSERT INTO public.schema_migrations (version, checksum, source)
            VALUES ($1, $2, 'adopted')
            ON CONFLICT (version) DO NOTHING
            """,
            version,
            migration.checksum,
        )
    return detected


async def _load_applied(conn: asyncpg.Connection) -> dict[str, str]:
    rows = await conn.fetch(
        "SELECT version, checksum FROM public.schema_migrations ORDER BY version"
    )
    return {row["version"]: row["checksum"] for row in rows}


def _validate_checksums(applied: dict[str, str], migrations: list[Migration]) -> None:
    expected = {migration.version: migration.checksum for migration in migrations}
    removed = sorted(set(applied) - set(expected))
    if removed:
        raise RuntimeError(
            f"database records migrations absent from this checkout: {', '.join(removed)}"
        )
    changed = [
        version
        for version, checksum in applied.items()
        if expected[version] != checksum
    ]
    if changed:
        raise RuntimeError(
            f"migration checksum mismatch for: {', '.join(sorted(changed))}"
        )


async def status(database_url: str | None = None) -> dict[str, object]:
    migrations = discover_migrations()
    db = Database(database_url)
    try:
        await db.connect()
        async with db._pool.acquire() as conn:
            table_exists = bool(
                await conn.fetchval(
                    "SELECT to_regclass('public.schema_migrations') IS NOT NULL"
                )
            )
            applied: dict[str, str] = {}
            if table_exists:
                applied = await _load_applied(conn)
            _validate_checksums(applied, migrations)
            pending = [
                migration.name
                for migration in migrations
                if migration.version not in applied
            ]
            return {
                "ok": True,
                "current": sorted(applied),
                "pending": pending,
                "table_exists": table_exists,
            }
    finally:
        await db.close()


async def up(database_url: str | None = None) -> dict[str, object]:
    migrations = discover_migrations()
    db = Database(database_url)
    applied_now: list[str] = []
    adopted: list[str] = []
    try:
        await db.connect()
        async with db._pool.acquire() as conn:
            await conn.execute("SELECT pg_advisory_lock($1)", ADVISORY_LOCK_ID)
            try:
                await _ensure_migration_table(conn)
                applied = await _load_applied(conn)
                if not applied:
                    adopted = await _adopt_existing_schema(conn, migrations)
                    applied = await _load_applied(conn)
                _validate_checksums(applied, migrations)

                pending = [
                    migration
                    for migration in migrations
                    if migration.version not in applied
                ]
                for migration in pending:
                    started = time.monotonic()
                    sql = migration.path.read_text(encoding="utf-8")
                    async with conn.transaction():
                        await conn.execute(sql)
                        await conn.execute(
                            """
                            INSERT INTO public.schema_migrations
                                (version, checksum, source, duration_ms)
                            VALUES ($1, $2, 'runner', $3)
                            """,
                            migration.version,
                            migration.checksum,
                            max(0, int((time.monotonic() - started) * 1000)),
                        )
                    applied_now.append(migration.name)

                final = await _load_applied(conn)
                _validate_checksums(final, migrations)
                return {
                    "ok": True,
                    "adopted": adopted,
                    "applied": applied_now,
                    "current": sorted(final),
                }
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", ADVISORY_LOCK_ID)
    finally:
        await db.close()


async def verify(database_url: str | None = None) -> dict[str, object]:
    migrations = discover_migrations()
    db = Database(database_url)
    try:
        await db.connect()
        async with db._pool.acquire() as conn:
            table_exists = bool(
                await conn.fetchval(
                    "SELECT to_regclass('public.schema_migrations') IS NOT NULL"
                )
            )
            if not table_exists:
                return {"ok": False, "error": "schema_migrations is missing"}
            applied = await _load_applied(conn)
            _validate_checksums(applied, migrations)
            missing = [
                migration.name
                for migration in migrations
                if migration.version not in applied
            ]
            if missing:
                return {"ok": False, "missing": missing}
            return {"ok": True, "versions": sorted(applied)}
    finally:
        await db.close()


async def up_with_retry(database_url: str | None = None) -> dict[str, object]:
    retries = max(1, int(os.getenv("LOGBOOK_MIGRATION_RETRIES", "60")))
    delay = max(0.1, float(os.getenv("LOGBOOK_MIGRATION_RETRY_DELAY", "1")))
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            return await up(database_url)
        except (OSError, asyncpg.PostgresError, RuntimeError) as exc:
            last_error = exc
            if attempt == retries:
                break
            await asyncio.sleep(delay)
    raise RuntimeError(
        f"database migration failed after {retries} attempts: {last_error}"
    ) from last_error


def _print_result(result: dict[str, object]) -> int:
    for key, value in result.items():
        print(f"{key}={value}")
    return 0 if result.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Logbook database migration runner")
    parser.add_argument(
        "command",
        choices=("status", "up", "verify"),
        help="inspect or apply versioned migrations",
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="override DATABASE_URL for this invocation",
    )
    args = parser.parse_args(argv)
    operations = {
        "status": status,
        "up": up,
        "verify": verify,
    }
    result = asyncio.run(operations[args.command](args.database_url))
    return _print_result(result)


if __name__ == "__main__":
    raise SystemExit(main())
