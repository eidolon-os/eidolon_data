from __future__ import annotations

import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text


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
        companion_columns = {
            column["name"] for column in inspector.get_columns("companions")
        }
        companion_indexes = {index["name"] for index in inspector.get_indexes("companions")}
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
    assert "companion_type" in companion_columns
    assert "ix_companions_companion_type" in companion_indexes
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


def test_companion_type_migration_backfills_from_is_master(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "eidolon.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_SQLITE_PATH", str(db_path))

    config = Config("alembic.ini")
    command.upgrade(config, "0007_event_classification")

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    """
                    INSERT INTO owners (
                        owner_id, display_name, kind, status,
                        profile_json, settings_json, created_at, updated_at
                    )
                    VALUES (
                        'owner-migration', 'Owner', 'person', 'active',
                        '{}', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                    )
                    """
                )
            )
            connection.execute(
                text(
                    """
                    INSERT INTO companions (
                        companion_id, owner_id, display_name, kind, status,
                        is_master, profile_json, runtime_config_json,
                        metadata_json, created_at, updated_at
                    )
                    VALUES
                        (
                            'companion-master', 'owner-migration', 'Master',
                            'companion', 'active', 1, '{}', '{}', '{}',
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        ),
                        (
                            'companion-slave', 'owner-migration', 'Slave',
                            'companion', 'active', 0, '{}', '{}', '{}',
                            CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                        )
                    """
                )
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            rows = {
                row.companion_id: row.companion_type
                for row in connection.execute(
                    text("SELECT companion_id, companion_type FROM companions")
                )
            }
    finally:
        engine.dispose()

    assert rows == {
        "companion-master": "master",
        "companion-slave": "slave",
    }
