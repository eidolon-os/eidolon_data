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
        conversation_columns = {
            column["name"] for column in inspector.get_columns("conversations")
        }
        turn_columns = {column["name"] for column in inspector.get_columns("turns")}
        runtime_caller_columns = {
            column["name"] for column in inspector.get_columns("runtime_callers")
        }
        runtime_session_columns = {
            column["name"] for column in inspector.get_columns("runtime_sessions")
        }
        body_command_columns = {
            column["name"] for column in inspector.get_columns("body_commands")
        }
        event_columns = {column["name"] for column in inspector.get_columns("events")}
        event_indexes = {index["name"] for index in inspector.get_indexes("events")}
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
        "body_commands",
        "runtime_callers",
        "runtime_sessions",
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
    assert {"caller_id", "actor_kind", "actor_id", "last_seen_at"}.issubset(
        runtime_caller_columns
    )
    assert {"session_id", "runtime_caller_id", "transport", "last_seen_at"}.issubset(
        runtime_session_columns
    )
    assert {"runtime_caller_id", "runtime_session_id", "source_device_id"}.issubset(
        body_command_columns
    )
    assert "runtime_caller_id" in conversation_columns
    assert "runtime_session_id" in conversation_columns
    assert "source_device_id" in conversation_columns
    assert "device_id" not in conversation_columns
    assert "runtime_caller_id" in turn_columns
    assert "runtime_session_id" in turn_columns
    assert "source_device_id" in turn_columns
    assert "device_id" not in turn_columns

    # 0007 event classification/correlation columns (see docs §3–4).
    assert {
        "companion_id",
        "event_class",
        "source",
        "severity",
        "outcome",
        "reason",
        "trace_id",
        "data_classification",
        "schema_version",
        "occurred_at",
    }.issubset(event_columns)
    assert {
        "ix_events_source",
        "ix_events_trace_id",
        "ix_events_owner_created",
    }.issubset(event_indexes)

    assert os.environ["EIDOLON_DATA_SQLITE_PATH"] == str(db_path)
