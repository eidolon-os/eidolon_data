from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import inspect

from eidolon_data import DataSettings, DataStore


async def test_init_schema_creates_core_tables(tmp_path) -> None:
    store = DataStore.open(DataSettings(sqlite_path=str(tmp_path / "eidolon_data.sqlite3")))
    try:
        await store.init_schema()

        async with store.engine.connect() as conn:
            table_names = await conn.run_sync(lambda sync_conn: inspect(sync_conn).get_table_names())
            columns = await conn.run_sync(
                lambda sync_conn: {
                    table: inspect(sync_conn).get_columns(table)
                    for table in (
                        "owners",
                        "devices",
                        "body_commands",
                    )
                }
            )
            persona_columns = await conn.run_sync(
                lambda sync_conn: {
                    column["name"]
                    for column in inspect(sync_conn).get_columns("persona_genomes")
                }
            )
            body_command_foreign_keys = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_foreign_keys("body_commands")
            )

        assert {
            "owners",
            "companions",
            "persona_genomes",
            "devices",
            "memory_realms",
            "body_commands",
            "owner_face_profile_revisions",
            "owner_face_references",
            "guard_owner_face_profile_deliveries",
        }.issubset(set(table_names))
        assert "persona_presets" not in table_names
        assert "credentials" not in table_names
        assert "grants" not in table_names
        assert "memory_items" not in table_names
        assert "memory_projections" not in table_names
        assert "storage_objects" not in table_names
        assert {
            "runtime_sessions",
            "conversations",
            "turns",
            "messages",
            "jobs",
            "events",
        }.isdisjoint(table_names)
        column_names = {table: {column["name"] for column in table_columns} for table, table_columns in columns.items()}
        devices_owner = next(column for column in columns["devices"] if column["name"] == "owner_id")
        assert "status" in column_names["owners"]
        assert devices_owner["nullable"] is True
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
        assert "runtime_callers" not in table_names
        assert "runtime_session_id" in column_names["body_commands"]
        assert "runtime_caller_id" not in column_names["body_commands"]
        assert all(
            foreign_key["referred_table"] != "runtime_sessions"
            for foreign_key in body_command_foreign_keys
        )
    finally:
        await store.close()


async def test_init_schema_rejects_legacy_owner_face_tables(tmp_path) -> None:
    db_path = tmp_path / "legacy-owner-face.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE owner_face_profile_revisions (
                profile_revision_id TEXT PRIMARY KEY,
                created_by TEXT NOT NULL
            )
            """
        )

    store = DataStore.open(DataSettings(sqlite_path=str(db_path)))
    try:
        with pytest.raises(RuntimeError, match="non-canonical Guard schema") as exc_info:
            await store.init_schema()
        assert "created_by" in str(exc_info.value)
    finally:
        await store.close()


async def test_init_schema_rejects_legacy_guard_action_table(tmp_path) -> None:
    db_path = tmp_path / "legacy-guard-action.sqlite3"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE guard_policy_actions (
                action_id TEXT PRIMARY KEY,
                status TEXT NOT NULL
            )
            """
        )

    store = DataStore.open(DataSettings(sqlite_path=str(db_path)))
    try:
        with pytest.raises(RuntimeError, match="non-canonical Guard schema") as exc_info:
            await store.init_schema()
        assert "guard_policy_actions missing columns" in str(exc_info.value)
        assert "fact_type" in str(exc_info.value)
    finally:
        await store.close()
