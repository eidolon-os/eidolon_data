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
        guard_binding_columns = {
            column["name"] for column in inspector.get_columns("guard_bindings")
        }
        guard_action_columns = {
            column["name"] for column in inspector.get_columns("guard_policy_actions")
        }
        guard_runtime_delivery_columns = {
            column["name"] for column in inspector.get_columns("guard_runtime_deliveries")
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
        "body_commands",
        "runtime_callers",
        "runtime_sessions",
        "guard_bindings",
        "guard_policy_actions",
        "guard_runtime_deliveries",
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
        "schema_version",
        "genome_hash",
        "realizer_version",
        "applied_event_id",
        "change_summary",
    }.issubset(persona_columns)
    assert "prompt_markdown" not in persona_columns
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
    assert {
        "owner_id",
        "guard_companion_id",
        "device_id",
        "state",
        "policy_id",
        "config_revision",
    }.issubset(guard_binding_columns)
    assert {
        "action_id",
        "binding_id",
        "status",
        "ack_json",
        "fact_type",
        "replay_key",
        "command_id",
        "delivery_attempt_count",
        "last_error",
        "delivery_claim_token",
        "delivery_lease_expires_at",
        "next_attempt_at",
        "delivery_dead_lettered_at",
    }.issubset(guard_action_columns)
    assert {
        "binding_id",
        "device_id",
        "runtime_revision",
        "desired_runtime_state",
        "status",
        "command_id",
    }.issubset(guard_runtime_delivery_columns)

    assert os.environ["EIDOLON_DATA_SQLITE_PATH"] == str(db_path)


def test_data_contract_alignment_upgrades_legacy_rows(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_SQLITE_PATH", str(db_path))
    config = Config("alembic.ini")
    command.upgrade(config, "0008_guard_policy_action_outbox")

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO owners (
                    owner_id, display_name, kind, status, profile_json, settings_json, created_at, updated_at
                ) VALUES ('owner-legacy', 'Legacy Owner', 'person', 'active', '{}', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO companions (
                    companion_id, owner_id, display_name, kind, status, is_master,
                    current_genome_id, default_memory_realm_id, profile_json, runtime_config_json,
                    metadata_json, created_at, updated_at
                ) VALUES (
                    'companion-legacy', 'owner-legacy', 'Legacy', 'companion', 'active', 1,
                    'genome-legacy', NULL, '{}', '{}', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO persona_genomes (
                    genome_id, companion_id, version, status, base_genome_id, source_json,
                    genome_json, prompt_markdown, evolution_state_json, change_summary, created_at, updated_at
                ) VALUES (
                    'genome-legacy', 'companion-legacy', 1, 'committed', NULL, '{}',
                    '{}', '# legacy', '{}', 'legacy DSL', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO events (
                    event_id, owner_id, subject_type, subject_id, event_type, actor_type,
                    actor_id, payload_json, created_at
                ) VALUES (
                    'event-legacy', 'owner-legacy', 'companion', 'companion-legacy',
                    'companion.created', 'system', NULL, '{}', CURRENT_TIMESTAMP
                )
                """
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            companion_type, current_genome_id = connection.exec_driver_sql(
                "SELECT companion_type, current_genome_id FROM companions WHERE companion_id = 'companion-legacy'"
            ).one()
            persona_count = connection.exec_driver_sql("SELECT COUNT(*) FROM persona_genomes").scalar_one()
            event = connection.exec_driver_sql(
                "SELECT event_class, source, severity, outcome, occurred_at FROM events WHERE event_id = 'event-legacy'"
            ).one()
        columns = {column["name"] for column in inspect(engine).get_columns("persona_genomes")}
    finally:
        engine.dispose()

    assert companion_type == "master"
    assert current_genome_id is None
    assert persona_count == 0
    assert "prompt_markdown" not in columns
    assert "evolution_state_json" not in columns
    assert event[:4] == ("audit", "data", "info", "success")
    assert event.occurred_at is not None
