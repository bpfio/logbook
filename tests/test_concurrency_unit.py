import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from logbook.db import Database


class FakeConnection:
    def __init__(self, delay: float = 0.01):
        self.delay = delay
        self.execute_calls = 0
        self.fetchval_calls = 0

    async def execute(self, sql: str, *args):
        self.execute_calls += 1
        await asyncio.sleep(self.delay)

    async def fetchval(self, sql: str, *args):
        self.fetchval_calls += 1
        await asyncio.sleep(self.delay)
        return True


class UnreadyConnection:
    async def fetchval(self, sql: str, *args):
        return False


class FakeAcquire:
    def __init__(self, connection: FakeConnection):
        self.connection = connection

    async def __aenter__(self):
        return self.connection

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakePool:
    def __init__(self, connection: FakeConnection):
        self.connection = connection
        self._loop = asyncio.get_running_loop()

    def acquire(self):
        return FakeAcquire(self.connection)


class FakeCreatedPool:
    def __init__(self, loop):
        self._loop = loop
        self.closed = False

    def terminate(self):
        self.closed = True

    async def close(self):
        self.closed = True


async def test_concurrent_connect_creates_one_pool_and_leaks_none():
    created = []

    async def delayed_create_pool(**kwargs):
        pool = FakeCreatedPool(asyncio.get_running_loop())
        created.append(pool)
        await asyncio.sleep(0.05)
        return pool

    db = Database()
    with patch("logbook.db.asyncpg.create_pool", delayed_create_pool):
        await asyncio.gather(db.connect(), db.connect(), db.connect())

    leaked = [pool for pool in created if pool is not db._pool and not pool.closed]
    assert len(created) == 1
    assert leaked == []
    await db.close()


async def test_messaging_schema_concurrent_first_check_executes_once():
    connection = FakeConnection()
    db = Database()
    db._pool = FakePool(connection)
    with patch.object(db, "connect", new=AsyncMock()):
        await asyncio.gather(
            db.ensure_messaging_schema(),
            db.ensure_messaging_schema(),
            db.ensure_messaging_schema(),
        )
    assert connection.execute_calls == 0
    assert connection.fetchval_calls == 1
    assert db._messaging_schema_ensured is True


async def test_messaging_schema_requires_versioned_migration():
    db = Database()
    db._pool = FakePool(UnreadyConnection())
    with patch.object(db, "connect", new=AsyncMock()):
        try:
            await db.ensure_messaging_schema()
        except RuntimeError as exc:
            assert "migrate up" in str(exc)
        else:
            raise AssertionError("unmigrated messaging schema was accepted")


async def test_project_schema_concurrent_first_check_executes_once():
    connection = FakeConnection()
    db = Database()
    db._pool = FakePool(connection)
    with patch.object(db, "connect", new=AsyncMock()):
        await asyncio.gather(
            db.ensure_project("probe"),
            db.ensure_project("probe"),
            db.ensure_project("probe"),
        )
        await db.ensure_project("probe")
    assert connection.execute_calls == 1
    assert "probe" in db._project_ready
