import asyncio
import os
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import asyncpg
import pytest

from logbook import migrate

pytestmark = pytest.mark.integration


def _with_database(url: str, database: str) -> str:
    parsed = urlparse(url)
    return urlunparse(parsed._replace(path=f"/{database}"))


def _admin_url(url: str) -> str:
    return _with_database(url, "postgres")


async def _create_database(admin_url: str, database: str) -> str:
    conn = await asyncpg.connect(admin_url)
    try:
        await conn.execute(f'DROP DATABASE IF EXISTS "{database}"')
        await conn.execute(f'CREATE DATABASE "{database}"')
    finally:
        await conn.close()
    return _with_database(admin_url, database)


async def _verify_objects(database_url: str) -> None:
    conn = await asyncpg.connect(database_url)
    try:
        expected = {
            "shared.projects": True,
            "shared.agent_messages": True,
            "shared.file_leases": True,
            "shared.researches": True,
            "public.schema_migrations": True,
        }
        for identifier, present in expected.items():
            assert bool(await conn.fetchval("SELECT to_regclass($1) IS NOT NULL", identifier)) is present
        assert bool(
            await conn.fetchval(
                "SELECT to_regprocedure('shared.init_project_schema(text)') IS NOT NULL"
            )
        ) is True
        assert await conn.fetchval(
            "SELECT count(*) FROM information_schema.columns "
            "WHERE table_schema = 'shared' AND table_name = 'devlogs' AND column_name = 'agent_ip'"
        ) == 1
        assert await conn.fetchval("SELECT count(*) FROM public.schema_migrations") == 10
    finally:
        await conn.close()


@pytest.mark.asyncio
async def test_clean_database_migrates_twice_and_verifies():
    base_url = os.environ["DATABASE_URL"]
    database_url = await _create_database(_admin_url(base_url), "migrate_clean_probe")

    before = await migrate.verify(database_url)
    assert before["ok"] is False

    first = await migrate.up(database_url)
    assert first["ok"] is True
    assert first["adopted"] == []
    assert len(first["applied"]) == 10

    second = await migrate.up(database_url)
    assert second["ok"] is True
    assert second["adopted"] == []
    assert second["applied"] == []

    assert (await migrate.verify(database_url))["ok"] is True
    await _verify_objects(database_url)


@pytest.mark.asyncio
async def test_existing_partial_schema_is_adopted_then_completed():
    base_url = os.environ["DATABASE_URL"]
    database_url = await _create_database(_admin_url(base_url), "migrate_adopt_probe")

    baseline = Path(migrate.migrations_dir() / "01_init.sql").read_text(encoding="utf-8")
    conn = await asyncpg.connect(database_url)
    try:
        await conn.execute(baseline)
    finally:
        await conn.close()

    result = await migrate.up(database_url)
    assert result["ok"] is True
    assert result["adopted"] == ["01", "02"]
    assert len(result["applied"]) == 8
    assert (await migrate.verify(database_url))["ok"] is True
    await _verify_objects(database_url)


@pytest.mark.asyncio
async def test_concurrent_up_applies_each_migration_once():
    base_url = os.environ["DATABASE_URL"]
    database_url = await _create_database(_admin_url(base_url), "migrate_concurrent_probe")

    results = await asyncio.gather(
        migrate.up(database_url),
        migrate.up(database_url),
    )
    assert all(result["ok"] for result in results)
    assert sum(len(result["applied"]) for result in results) == 10
    assert (await migrate.verify(database_url))["ok"] is True
    await _verify_objects(database_url)
