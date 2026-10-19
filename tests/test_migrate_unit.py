from pathlib import Path

import pytest

from logbook.migrate import discover_migrations


def test_discover_current_migration_chain():
    migrations = discover_migrations()
    assert [migration.version for migration in migrations] == [f"{index:02d}" for index in range(1, 13)]
    assert len({migration.checksum for migration in migrations}) == len(migrations)


def test_discover_rejects_version_gaps(tmp_path: Path):
    (tmp_path / "01_first.sql").write_text("SELECT 1;", encoding="utf-8")
    (tmp_path / "03_third.sql").write_text("SELECT 3;", encoding="utf-8")
    with pytest.raises(ValueError, match="contiguous"):
        discover_migrations(tmp_path)
