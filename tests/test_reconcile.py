"""Additive schema-reconcile behaviour (see db/engine._reconcile_schema_sync).

Uses an isolated MetaData with an "old" and a "new" version of one table so the
reconcile logic is exercised in isolation from the real Base schema.
"""

from __future__ import annotations

from sqlalchemy import Column, Integer, MetaData, String, Table, inspect, text
from sqlalchemy.ext.asyncio import create_async_engine

from eidolon_data.db.engine import _reconcile_schema_sync


def _old_metadata() -> MetaData:
    md = MetaData()
    Table(
        "recon_probe",
        md,
        Column("id", Integer, primary_key=True),
        Column("name", String(32)),
    )
    return md


def _new_metadata() -> MetaData:
    md = MetaData()
    Table(
        "recon_probe",
        md,
        Column("id", Integer, primary_key=True),
        Column("name", String(32)),
        Column("note", String(64)),  # nullable → added
        Column("tag", String(16), nullable=False, default="x"),  # notnull+scalar default → added w/ backfill
        Column("flagged", Integer, index=True),  # nullable + index → added + index
        Column("code", String(32), unique=True),  # unique → skipped
        Column("hardnn", String(32), nullable=False),  # notnull, no default → skipped
    )
    return md


async def _columns(engine, table: str) -> dict[str, dict]:
    async with engine.connect() as conn:
        return await conn.run_sync(
            lambda c: {col["name"]: col for col in inspect(c).get_columns(table)}
        )


async def _indexes(engine, table: str) -> set[str]:
    async with engine.connect() as conn:
        rows = await conn.run_sync(lambda c: inspect(c).get_indexes(table))
    return {idx["name"] for idx in rows}


async def test_reconcile_adds_safe_columns_and_skips_unsafe(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recon.sqlite3'}")
    try:
        # Old schema + an existing row, to prove the NOT-NULL default backfills.
        async with engine.begin() as conn:
            await conn.run_sync(_old_metadata().create_all)
            await conn.execute(text("INSERT INTO recon_probe (id, name) VALUES (1, 'row1')"))

        async with engine.begin() as conn:
            await conn.run_sync(_reconcile_schema_sync, _new_metadata())

        cols = await _columns(engine, "recon_probe")
        # additive-safe columns were added
        assert "note" in cols
        assert "tag" in cols
        assert "flagged" in cols
        # unsafe columns were skipped (need a real migration)
        assert "code" not in cols
        assert "hardnn" not in cols

        # NOT-NULL scalar default backfilled the pre-existing row
        async with engine.connect() as conn:
            tag = (await conn.execute(text("SELECT tag FROM recon_probe WHERE id=1"))).scalar_one()
        assert tag == "x"

        # index recreated for the indexed reconciled column
        assert "ix_recon_probe_flagged" in await _indexes(engine, "recon_probe")

        # idempotent: a second pass is a no-op and does not raise
        async with engine.begin() as conn:
            await conn.run_sync(_reconcile_schema_sync, _new_metadata())
        cols_again = await _columns(engine, "recon_probe")
        assert set(cols_again) == set(cols)
    finally:
        await engine.dispose()
