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
        body_command_columns = {
            column["name"] for column in inspector.get_columns("body_commands")
        }
        body_command_foreign_keys = inspector.get_foreign_keys("body_commands")
        guard_binding_columns = {
            column["name"] for column in inspector.get_columns("guard_bindings")
        }
        guard_binding_indexes = {
            index["name"]: index for index in inspector.get_indexes("guard_bindings")
        }
        guard_action_columns = {
            column["name"] for column in inspector.get_columns("guard_policy_actions")
        }
        guard_runtime_delivery_columns = {
            column["name"] for column in inspector.get_columns("guard_runtime_deliveries")
        }
        owner_face_profile_columns = {
            column["name"] for column in inspector.get_columns("owner_face_profile_revisions")
        }
        owner_face_reference_columns = {
            column["name"] for column in inspector.get_columns("owner_face_references")
        }
        owner_face_delivery_columns = {
            column["name"]
            for column in inspector.get_columns("guard_owner_face_profile_deliveries")
        }
        owner_face_profile_checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints(
                "owner_face_profile_revisions"
            )
        }
        owner_face_reference_checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints("owner_face_references")
        }
        owner_face_delivery_checks = {
            constraint["name"]
            for constraint in inspector.get_check_constraints(
                "guard_owner_face_profile_deliveries"
            )
        }
    finally:
        engine.dispose()

    assert {
        "owners",
        "companions",
        "persona_genomes",
        "devices",
        "memory_realms",
        "body_commands",
        "guard_bindings",
        "guard_policy_actions",
        "guard_runtime_deliveries",
        "owner_face_profile_revisions",
        "owner_face_references",
        "guard_owner_face_profile_deliveries",
        "alembic_version",
    }.issubset(tables)
    assert "persona_presets" not in tables
    assert "credentials" not in tables
    assert "grants" not in tables
    assert "memory_items" not in tables
    assert "memory_projections" not in tables
    assert "storage_objects" not in tables
    assert {
        "runtime_sessions",
        "conversations",
        "turns",
        "messages",
        "jobs",
        "events",
    }.isdisjoint(tables)
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
    assert "runtime_callers" not in tables
    assert {"runtime_session_id", "source_device_id"}.issubset(
        body_command_columns
    )
    assert all(
        foreign_key["referred_table"] != "runtime_sessions"
        for foreign_key in body_command_foreign_keys
    )
    assert {
        "owner_id",
        "guard_companion_id",
        "device_id",
        "state",
        "policy_id",
        "config_revision",
    }.issubset(guard_binding_columns)
    assert guard_binding_indexes["uq_guard_bindings_owner_device"]["unique"] == 1
    assert guard_binding_indexes["uq_guard_bindings_guard_companion_active"]["unique"] == 1
    assert "uq_guard_bindings_owner_active" not in guard_binding_indexes
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
    assert owner_face_profile_columns == {
        "profile_revision_id",
        "profile_id",
        "owner_id",
        "revision",
        "state",
        "desired_state",
        "model_id",
        "preprocessing_version",
        "activated_at",
        "created_at",
        "updated_at",
    }
    assert owner_face_reference_columns == {
        "reference_id",
        "profile_revision_id",
        "pose",
        "content_type",
        "size_bytes",
        "sha256",
        "storage_key",
        "created_at",
    }
    assert owner_face_delivery_columns == {
        "delivery_id",
        "binding_id",
        "profile_revision_id",
        "status",
        "command_id",
        "attempt_count",
        "last_error",
        "lease_expires_at",
        "dispatched_at",
        "applied_at",
        "created_at",
        "updated_at",
    }
    assert {
        "ck_owner_face_profile_revisions_ck_owner_face_profile_revision_positive",
        "ck_owner_face_profile_revisions_ck_owner_face_profile_state",
        "ck_owner_face_profile_revisions_ck_owner_face_profile_desired_state",
        "ck_owner_face_profile_revisions_ck_owner_face_profile_model_state",
    }.issubset(owner_face_profile_checks)
    assert {
        "ck_owner_face_references_ck_owner_face_reference_pose",
        "ck_owner_face_references_ck_owner_face_reference_content_type",
        "ck_owner_face_references_ck_owner_face_reference_size_positive",
    }.issubset(owner_face_reference_checks)
    assert {
        "ck_guard_owner_face_profile_deliveries_ck_guard_owner_face_profile_delivery_status",
        "ck_guard_owner_face_profile_deliveries_ck_guard_owner_face_attempt_count",
    }.issubset(owner_face_delivery_checks)

    assert os.environ["EIDOLON_DATA_SQLITE_PATH"] == str(db_path)


def test_data_contract_alignment_discards_retired_legacy_rows(
    tmp_path: Path, monkeypatch
) -> None:
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
                    event_id, owner_id, subject_type, subject_id, event_type,
                    payload_json, created_at
                ) VALUES (
                    'event-legacy', 'owner-legacy', 'companion', 'companion-legacy',
                    'companion.created', '{}', CURRENT_TIMESTAMP
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
        inspector = inspect(engine)
        columns = {
            column["name"] for column in inspector.get_columns("persona_genomes")
        }
        tables = set(inspector.get_table_names())
    finally:
        engine.dispose()

    assert companion_type == "master"
    assert current_genome_id is None
    assert persona_count == 0
    assert "prompt_markdown" not in columns
    assert "evolution_state_json" not in columns
    assert "events" not in tables


def test_multi_guard_migration_backfills_legacy_guard_workspace(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "legacy-guard-workspace.sqlite3"
    monkeypatch.setenv("EIDOLON_DATA_SQLITE_PATH", str(db_path))
    config = Config("alembic.ini")
    command.upgrade(config, "0015_guard_binding_identity")

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO owners (
                    owner_id, display_name, kind, status, profile_json, settings_json, created_at, updated_at
                ) VALUES ('owner-guard-legacy', 'Guard Owner', 'person', 'active', '{}', '{}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """
            )
            connection.exec_driver_sql(
                """
                INSERT INTO companions (
                    companion_id, owner_id, display_name, kind, status, is_master, companion_type,
                    current_genome_id, default_memory_realm_id, profile_json, runtime_config_json,
                    metadata_json, created_at, updated_at
                ) VALUES (
                    'guard-legacy', 'owner-guard-legacy', 'Legacy Guard', 'guard', 'active', 0, 'slave',
                    NULL, NULL, '{}', '{}', '{"guard_control_plane": true}', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")

    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as connection:
            companion = connection.exec_driver_sql(
                """
                SELECT companion_type, current_genome_id, default_memory_realm_id
                FROM companions WHERE companion_id = 'guard-legacy'
                """
            ).one()
            genome = connection.exec_driver_sql(
                "SELECT companion_id, status FROM persona_genomes WHERE genome_id = ?",
                (companion.current_genome_id,),
            ).one()
            realm = connection.exec_driver_sql(
                "SELECT companion_id, owner_id, status FROM memory_realms WHERE realm_id = ?",
                (companion.default_memory_realm_id,),
            ).one()
    finally:
        engine.dispose()

    assert companion.companion_type == "guard"
    assert genome == ("guard-legacy", "committed")
    assert realm == ("guard-legacy", "owner-guard-legacy", "active")
