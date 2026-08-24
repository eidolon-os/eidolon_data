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


def test_database_constraints_enforce_the_three_identity_axes(tmp_path, monkeypatch) -> None:
    """kind, lifecycle_state and the Owner's default pointer are independent.

    They used to be one column. ``role='primary'`` meant both "this is the
    default" and "this is a plain conversational companion", so a guard could
    not be a default and changing the default rewrote a type. The schema now
    refuses values in each axis on its own, and "which one is the default" is a
    pointer on the Owner rather than a flag needing a cross-row uniqueness rule.
    """
    path = tmp_path / "constraints.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_DATABASE_URL", f"sqlite+aiosqlite:///{path}")
    command.upgrade(Config("alembic.ini"), "head")
    with closing(sqlite3.connect(path)) as connection:
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute(
            "INSERT INTO owners(owner_id, display_name) VALUES ('owner-a', 'A'), ('owner-b', 'B')"
        )
        for column, bad in (("kind", "primary"), ("lifecycle_state", "inactive")):
            # 'primary' and 'inactive' are the two values the old column
            # carried that no longer mean anything: one was a routing fact, the
            # other conflated "cannot run" with "the Owner archived it".
            with pytest.raises(sqlite3.IntegrityError):
                connection.execute(
                    f"INSERT INTO companions(companion_id, owner_id, {column}) "
                    f"VALUES ('bad-{column}', 'owner-a', '{bad}')"
                )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO companions(companion_id, owner_id, revision) "
                "VALUES ('bad-revision', 'owner-a', 0)"
            )
        connection.execute(
            "INSERT INTO companions(companion_id, owner_id, kind) "
            "VALUES ('companion-a', 'owner-a', 'guard')"
        )
        # A guard is a kind, so storing one is fine; whether it may be a default
        # is a rule above this layer, not a column value.
        connection.execute(
            "UPDATE owners SET default_companion_id = 'companion-a' WHERE owner_id = 'owner-a'"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE owners SET default_companion_id = 'no-such-companion' "
                "WHERE owner_id = 'owner-b'"
            )
        # A realm belongs to an Owner, so there is no cross-owner companion
        # pairing left to forbid. What the schema forbids instead is a second
        # active realm for one Owner — the state nothing downstream can resolve,
        # because routing would have to pick between two halves of one memory.
        connection.execute(
            "INSERT INTO memory_realms(realm_id, owner_id) VALUES ('realm-a', 'owner-a')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO memory_realms(realm_id, owner_id) "
                "VALUES ('realm-a-second', 'owner-a')"
            )
        # Retiring the first one frees the Owner to have another.
        connection.execute(
            "UPDATE memory_realms SET status = 'inactive' WHERE realm_id = 'realm-a'"
        )
        connection.execute(
            "INSERT INTO memory_realms(realm_id, owner_id) "
            "VALUES ('realm-a-second', 'owner-a')"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO memory_realms(realm_id, owner_id) "
                "VALUES ('realm-no-owner', 'owner-missing')"
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
