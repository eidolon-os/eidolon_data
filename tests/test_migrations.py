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
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        owner_columns = {column["name"] for column in inspector.get_columns("owners")}
        device_owner_column = next(
            column for column in inspector.get_columns("devices") if column["name"] == "owner_id"
        )
        persona_columns = {
            column["name"] for column in inspector.get_columns("persona_genomes")
        }
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
    assert "status" in owner_columns
    assert device_owner_column["nullable"] is True
    assert {
        "status",
        "base_genome_id",
        "prompt_markdown",
        "change_summary",
    }.issubset(persona_columns)

    assert os.environ["EIDOLON_DATA_SQLITE_PATH"] == str(db_path)
