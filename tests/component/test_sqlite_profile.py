from __future__ import annotations

import asyncio
from time import perf_counter

import pytest
from sqlalchemy import text

from eidolon_data import DataSettings, DataStore
from eidolon_data.db.engine import create_engine

pytestmark = pytest.mark.component


async def test_sovereign_store_uses_durable_wal_profile(tmp_path) -> None:
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


async def test_rebuildable_store_may_choose_normal_synchronous(tmp_path) -> None:
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


async def test_burst_of_low_frequency_writes_is_serialized_without_lock_failures(tmp_path) -> None:
    authority = DataStore.open(
        DataSettings(
            sqlite_path=str(tmp_path / "burst.sqlite3"),
            sqlite_pool_size=1,
            sqlite_busy_timeout_ms=5_000,
        )
    )
    await authority.init_schema()
    started = perf_counter()
    try:
        await asyncio.wait_for(
            asyncio.gather(
                *(
                    authority.owner_commands.create_owner(owner_id=f"owner-{index:03d}")
                    for index in range(50)
                )
            ),
            timeout=10,
        )
        assert len(await authority.owners.list()) == 50
        assert len(await authority.audit_outbox.list_pending(limit=100)) == 50
        assert perf_counter() - started < 10
    finally:
        await authority.close()
