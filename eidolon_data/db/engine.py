"""Engine/session construction for Eidolon Data."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from eidolon_data.db.base import Base
from eidolon_data.settings import DataSettings


def create_engine(settings: DataSettings) -> AsyncEngine:
    if settings.database_url.startswith("sqlite+aiosqlite:///"):
        path_text = settings.database_url.removeprefix("sqlite+aiosqlite:///")
        Path(path_text).expanduser().parent.mkdir(parents=True, exist_ok=True)
    return create_async_engine(settings.database_url, echo=settings.echo_sql)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_schema(engine: AsyncEngine) -> None:
    from eidolon_data.schema import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if conn.dialect.name == "sqlite":
            await _repair_sqlite_schema(conn)


async def _repair_sqlite_schema(conn) -> None:
    """Bring early local-first SQLite DBs up to the current v1 shape.

    ``create_all`` deliberately does not alter existing tables. Some developer
    databases were initialized before Alembic was wired, so they have core
    tables but miss later v1 columns. Keep this repair small and idempotent;
    formal forward migrations remain in ``db/migrations``.
    """

    owners = await _sqlite_columns(conn, "owners")
    if owners:
        await _sqlite_add_column(
            conn,
            "owners",
            owners,
            "status",
            "VARCHAR(32) NOT NULL DEFAULT 'active'",
        )
        await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_owners_status ON owners (status)"))

    devices = await _sqlite_columns(conn, "devices")
    if devices:
        await _sqlite_add_column(conn, "devices", devices, "approved_at", "DATETIME")
        await _sqlite_add_column(conn, "devices", devices, "approved_by", "VARCHAR(128)")
        await _sqlite_add_column(conn, "devices", devices, "bound_companion_id", "VARCHAR(64)")
        await _sqlite_add_column(conn, "devices", devices, "interaction_mode", "VARCHAR(64)")
        await _sqlite_add_column(conn, "devices", devices, "auth_type", "VARCHAR(32)")
        await _sqlite_add_column(conn, "devices", devices, "secret_ref", "TEXT")
        await _sqlite_add_column(
            conn,
            "devices",
            devices,
            "access_policy_json",
            "JSON NOT NULL DEFAULT '{}'",
        )
        await _sqlite_add_column(conn, "devices", devices, "revoked_at", "DATETIME")
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_devices_bound_companion_id "
                "ON devices (bound_companion_id)"
            )
        )

    conversations = await _sqlite_columns(conn, "conversations")
    if conversations:
        added_updated_at = "updated_at" not in conversations
        await _sqlite_add_column(conn, "conversations", conversations, "updated_at", "DATETIME")
        if added_updated_at:
            await conn.execute(
                text(
                    "UPDATE conversations "
                    "SET updated_at = COALESCE(updated_at, started_at, CURRENT_TIMESTAMP)"
                )
            )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_conversations_owner_updated "
                "ON conversations (owner_id, updated_at)"
            )
        )

    persona_genomes = await _sqlite_columns(conn, "persona_genomes")
    if persona_genomes:
        await _sqlite_add_column(
            conn,
            "persona_genomes",
            persona_genomes,
            "status",
            "VARCHAR(32) NOT NULL DEFAULT 'committed'",
        )
        await _sqlite_add_column(
            conn,
            "persona_genomes",
            persona_genomes,
            "base_genome_id",
            "VARCHAR(64)",
        )
        await _sqlite_add_column(
            conn,
            "persona_genomes",
            persona_genomes,
            "prompt_markdown",
            "TEXT NOT NULL DEFAULT ''",
        )
        await _sqlite_add_column(
            conn,
            "persona_genomes",
            persona_genomes,
            "change_summary",
            "TEXT NOT NULL DEFAULT ''",
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_persona_genomes_status "
                "ON persona_genomes (status)"
            )
        )
        await conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_persona_genomes_base_genome_id "
                "ON persona_genomes (base_genome_id)"
            )
        )


async def _sqlite_columns(conn, table_name: str) -> set[str]:
    rows = (await conn.execute(text(f"PRAGMA table_info({table_name})"))).mappings()
    return {str(row["name"]) for row in rows}


async def _sqlite_add_column(
    conn,
    table_name: str,
    columns: set[str],
    column_name: str,
    ddl: str,
) -> None:
    if column_name in columns:
        return
    await conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))
    columns.add(column_name)
