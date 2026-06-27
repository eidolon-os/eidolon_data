from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


def test_alembic_upgrade_head_creates_core_schema(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "eidolon.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_SQLITE_PATH", str(db_path))

    config = Config("alembic.ini")
    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        tables = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    assert {
        "owners",
        "companions",
        "persona_genomes",
        "devices",
        "conversations",
        "turns",
        "messages",
        "memory_realms",
        "jobs",
        "events",
        "alembic_version",
    }.issubset(tables)
    assert "persona_presets" not in tables
    assert "credentials" not in tables
    assert "grants" not in tables
    assert "memory_items" not in tables
    assert "memory_projections" not in tables
    assert "storage_objects" not in tables

    assert os.environ["EIDOLON_DATA_SQLITE_PATH"] == str(db_path)
