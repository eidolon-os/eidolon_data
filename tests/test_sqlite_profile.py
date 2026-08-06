from __future__ import annotations

from sqlalchemy import text

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
