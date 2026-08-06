from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from eidolon_data import DataStore
from eidolon_data.db.engine import create_engine
from eidolon_data.settings import DataSettings


async def test_authority_sqlite_profile_is_explicit(tmp_path) -> None:
    engine = create_engine(DataSettings(sqlite_path=str(tmp_path / "authority.sqlite3")))
    try:
        async with engine.connect() as connection:
            values = {
                name: (await connection.execute(text(f"PRAGMA {name}"))).scalar()
                for name in (
                    "journal_mode",
                    "synchronous",
                    "foreign_keys",
                    "busy_timeout",
                    "wal_autocheckpoint",
                )
            }
        assert values == {
            "journal_mode": "wal",
            "synchronous": 2,
            "foreign_keys": 1,
            "busy_timeout": 5_000,
            "wal_autocheckpoint": 1_000,
        }
    finally:
        await engine.dispose()


async def test_rebuildable_profile_can_choose_normal_synchronous(tmp_path) -> None:
    engine = create_engine(
        DataSettings(
            sqlite_path=str(tmp_path / "projection.sqlite3"),
            sqlite_synchronous="NORMAL",
        )
    )
    try:
        async with engine.connect() as connection:
            assert (await connection.execute(text("PRAGMA synchronous"))).scalar() == 1
    finally:
        await engine.dispose()


async def test_query_only_consumer_cannot_mutate_system_data(tmp_path) -> None:
    path = tmp_path / "eidolon-system.sqlite3"
    writer = DataStore.open(DataSettings(sqlite_path=str(path)))
    await writer.init_schema()
    await writer.owner_service.create_owner(owner_id="owner-read-only")
    await writer.close()

    reader = DataStore.open(
        DataSettings(sqlite_path=str(path), sqlite_read_only=True)
    )
    try:
        await reader.validate_schema()
        assert await reader.owners.get("owner-read-only") is not None
        with pytest.raises(RuntimeError, match="cannot initialize schema"):
            await reader.init_schema()
        with pytest.raises(OperationalError):
            await reader.owner_service.create_owner(owner_id="owner-write-denied")
    finally:
        await reader.close()
