import asyncio

import pytest

from logbook.db import db

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_file_lease_concurrent_rounds_have_exactly_one_winner():
    await db.ensure_messaging_schema()
    both_succeeded = 0
    only_one_succeeded = 0
    for index in range(100):
        path = f"src/concurrency/{index}.py"
        results = await asyncio.gather(
            db.acquire_file_lease("concurrency_probe", "agent_a", path),
            db.acquire_file_lease("concurrency_probe", "agent_b", path),
        )
        successes = sum(1 for success, _, _ in results if success)
        both_succeeded += successes == 2
        only_one_succeeded += successes == 1

    assert both_succeeded == 0
    assert only_one_succeeded == 100
