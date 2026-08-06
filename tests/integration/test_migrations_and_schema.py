from __future__ import annotations

import sqlite3
from contextlib import closing

import pytest
from alembic import command
from alembic.config import Config

import eidolon_data.schema  # noqa: F401
from eidolon_data import DataSettings, DataStore
from eidolon_data.db.base import Base

pytestmark = pytest.mark.integration

EXPECTED_TABLES = set(Base.metadata.tables)


def test_fresh_alembic_upgrade_matches_current_model_exactly(tmp_path, monkeypatch) -> None:
    path = tmp_path / "migrated.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    command.upgrade(Config("alembic.ini"), "head")

    with closing(sqlite3.connect(path)) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert tables == EXPECTED_TABLES | {"alembic_version"}
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0001_system_data_v2",
        )
        for table_name, table in Base.metadata.tables.items():
            actual_columns = {
                row[1] for row in connection.execute(f'PRAGMA table_info("{table_name}")')
            }
            assert actual_columns == set(table.columns.keys()), table_name
    command.check(Config("alembic.ini"))


def test_clean_baseline_can_downgrade_and_reapply(tmp_path, monkeypatch) -> None:
    path = tmp_path / "migration-cycle.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.downgrade(config, "base")
    with closing(sqlite3.connect(path)) as connection:
        remaining = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
            )
        }
        assert remaining == {"alembic_version"}
    command.upgrade(config, "head")
    with closing(sqlite3.connect(path)) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "0001_system_data_v2",
        )


async def test_migrated_database_passes_fail_closed_schema_validation(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "validated.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    # Alembic's env owns an event loop, so run the synchronous command in a thread.
    import asyncio

    await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "head")
    store = DataStore.open(DataSettings(sqlite_path=str(path), sqlite_read_only=True))
    try:
        await store.validate_schema()
    finally:
        await store.close()


async def test_schema_validation_rejects_retired_or_unknown_tables(tmp_path) -> None:
    path = tmp_path / "drift.sqlite3"
    store = DataStore.open(DataSettings(sqlite_path=str(path)))
    await store.init_schema()
    await store.close()
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("CREATE TABLE devices (device_id TEXT PRIMARY KEY)")
        connection.commit()

    drifted = DataStore.open(DataSettings(sqlite_path=str(path)))
    try:
        with pytest.raises(RuntimeError, match="devices"):
            await drifted.validate_schema()
    finally:
        await drifted.close()


def test_database_constraints_enforce_role_and_cross_owner_integrity(tmp_path, monkeypatch) -> None:
    path = tmp_path / "constraints.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    command.upgrade(Config("alembic.ini"), "head")
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO owners(owner_id, display_name) VALUES ('owner-a', 'A'), ('owner-b', 'B')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO companions(companion_id, owner_id, role) "
                "VALUES ('bad-role', 'owner-a', 'master')"
            )
        connection.execute(
            "INSERT INTO companions(companion_id, owner_id, role) "
            "VALUES ('companion-a', 'owner-a', 'standard')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO memory_realms(realm_id, owner_id, companion_id) "
                "VALUES ('realm-cross-owner', 'owner-b', 'companion-a')"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO companion_face_assets("
                "face_asset_id, companion_id, owner_id, version, cond_storage_key, "
                "cond_content_type, cond_size_bytes, cond_sha256"
                ") VALUES ("
                "'face-cross-owner', 'companion-a', 'owner-b', 1, 'cross-owner.jpg', "
                "'image/jpeg', 1, "
                "'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'"
                ")"
            )
