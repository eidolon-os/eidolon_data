from __future__ import annotations

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
                    for table in ("owners", "devices", "conversations", "turns", "messages")
                }
            )
            persona_columns = await conn.run_sync(
                lambda sync_conn: {
                    column["name"]
                    for column in inspect(sync_conn).get_columns("persona_genomes")
                }
            )
            unique_constraints = await conn.run_sync(
                lambda sync_conn: inspect(sync_conn).get_unique_constraints("messages")
            )

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
        }.issubset(set(table_names))
        assert "persona_presets" not in table_names
        assert "credentials" not in table_names
        assert "grants" not in table_names
        assert "memory_items" not in table_names
        assert "memory_projections" not in table_names
        assert "storage_objects" not in table_names
        column_names = {table: {column["name"] for column in table_columns} for table, table_columns in columns.items()}
        devices_owner = next(column for column in columns["devices"] if column["name"] == "owner_id")
        assert "status" in column_names["owners"]
        assert devices_owner["nullable"] is True
        assert {
            "status",
            "base_genome_id",
            "prompt_markdown",
            "change_summary",
        }.issubset(persona_columns)
        assert "updated_at" in column_names["conversations"]
        assert "device_id" in column_names["turns"]
        assert "seq" in column_names["messages"]
        assert any(
            constraint["name"] == "uq_messages_turn_seq"
            for constraint in unique_constraints
        )
    finally:
        await store.close()

