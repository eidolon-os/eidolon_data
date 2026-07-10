from __future__ import annotations

import os
import json
from pathlib import Path

import pytest
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
        persona_column_rows = inspector.get_columns("persona_genomes")
        persona_columns = {column["name"] for column in persona_column_rows}
        persona_defaults = {
            column["name"]: column["default"] for column in persona_column_rows
        }
        persona_checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("persona_genomes")
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
        "schema_version",
        "genome_hash",
        "realizer_version",
        "applied_event_id",
        "change_summary",
    }.issubset(persona_columns)
    assert "compiler_version" not in persona_columns
    assert "stable_prompt_hash" not in persona_columns
    assert "eidolon.persona_genome" in persona_defaults["schema_version"]
    assert "eidolon.persona_realizer" in persona_defaults["realizer_version"]
    assert {
        "ck_persona_genomes_schema_current",
        "ck_persona_genomes_realizer_current",
    }.issubset(persona_checks)
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


def test_v1_persona_rows_are_discarded_instead_of_semantically_guessed(
    tmp_path: Path, monkeypatch
) -> None:
    db_path = tmp_path / "eidolon-v1.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_SQLITE_PATH", str(db_path))
    config = Config("alembic.ini")
    command.upgrade(config, "0009_persona_genome_v1")

    engine = create_engine(f"sqlite:///{db_path}")
    v1 = {
        "schema_version": "eidolon.persona_genome.v1",
        "identity_core": {
            "name": "Yi",
            "archetype": "companion",
            "description": "Quietly perceptive.",
            "values": ["honesty"],
            "boundaries": ["never invent memory"],
        },
        "relationship": {
            "stage": "new",
            "owner_preferences": {"relationship": "long-term partner"},
            "pinned_facts": ["Owner builds Eidolon"],
            "safety_boundaries": [],
        },
        "traits": {
            "core.playfulness": {
                "value": 0.6,
                "confidence": 0.8,
                "source": "owner",
                "min": 0.0,
                "max": 1.0,
            }
        },
        "style_compiler": {
            "base_instructions": ["Be concise."],
            "trait_mappings": {"core.playfulness": []},
            "spoken_phrases": [],
        },
        "memory_adapter": {"recall_policy": {}, "relation_policies": {}},
        "evolution_policy": {},
        "provenance": {"origin": "owner_authored", "evidence_refs": []},
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                "ALTER TABLE persona_genomes ADD COLUMN realizer_version "
                "VARCHAR(64) NOT NULL DEFAULT 'eidolon.persona_realizer'"
            )
        )
        connection.execute(
            text(
                "INSERT INTO owners (owner_id, display_name, kind, status, profile_json, "
                "settings_json, created_at, updated_at) VALUES "
                "('o1', 'Owner', 'person', 'active', '{}', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
        connection.execute(
            text(
                "INSERT INTO companions (companion_id, owner_id, display_name, kind, status, "
                "profile_json, runtime_config_json, metadata_json, created_at, updated_at, "
                "is_master, companion_type) VALUES "
                "('c1', 'o1', 'Yi', 'companion', 'active', '{}', '{}', '{}', "
                "CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1, 'master')"
            )
        )
        connection.execute(
            text(
                "INSERT INTO persona_genomes (genome_id, companion_id, version, status, "
                "schema_version, genome_hash, compiler_version, source_json, genome_json, "
                "change_summary, created_at, updated_at) VALUES "
                "(:id, 'c1', 1, 'committed', 'eidolon.persona_genome.v1', 'old_hash', "
                "'eidolon.persona_compiler.v1', '{}', :genome, '', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            ),
            {"id": "g1", "genome": json.dumps(v1)},
        )
        connection.execute(
            text("UPDATE companions SET current_genome_id = 'g1' WHERE companion_id = 'c1'")
        )
        connection.execute(
            text(
                "INSERT INTO events (event_id, owner_id, companion_id, subject_type, "
                "subject_id, event_type, event_class, source, severity, outcome, actor_type, "
                "data_classification, schema_version, payload_json, occurred_at, created_at) "
                "VALUES ('evt-g1-commit', 'o1', 'c1', 'persona_genome', 'g1', "
                "'persona.genome.committed', 'audit', 'data', 'info', 'success', 'system', "
                "'safe', 1, '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
            )
        )
    engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            assert connection.execute(
                text("SELECT COUNT(*) FROM persona_genomes")
            ).scalar_one() == 0
            assert connection.execute(
                text("SELECT current_genome_id FROM companions WHERE companion_id = 'c1'")
            ).scalar_one() is None
    finally:
        engine.dispose()


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
