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

    companions = await _sqlite_columns(conn, "companions")
    if companions:
        await conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_companions_owner_companion "
                "ON companions (owner_id, companion_id)"
            )
        )

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
        await _sqlite_rebuild_devices_if_needed(conn)

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
        await _sqlite_rebuild_conversations_if_needed(conn)

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


async def _sqlite_rebuild_devices_if_needed(conn) -> None:
    rows = list((await conn.execute(text("PRAGMA table_info(devices)"))).mappings())
    owner = next((row for row in rows if row["name"] == "owner_id"), None)
    needs_owner_nullable = owner is not None and bool(int(owner["notnull"] or 0))
    needs_companion_fk = not await _sqlite_has_foreign_key(
        conn,
        "devices",
        from_columns=("owner_id", "bound_companion_id"),
        to_table="companions",
        to_columns=("owner_id", "companion_id"),
    )
    if owner is None or not (needs_owner_nullable or needs_companion_fk):
        return

    await conn.execute(text("ALTER TABLE devices RENAME TO devices_owner_required"))
    await conn.execute(
        text(
            """
            CREATE TABLE devices (
                device_id VARCHAR(128) NOT NULL,
                owner_id VARCHAR(64),
                name VARCHAR(128) NOT NULL,
                kind VARCHAR(64) NOT NULL,
                status VARCHAR(32) NOT NULL,
                approved_at DATETIME,
                approved_by VARCHAR(128),
                bound_companion_id VARCHAR(64),
                interaction_mode VARCHAR(64),
                auth_type VARCHAR(32),
                secret_ref TEXT,
                capabilities_json JSON NOT NULL,
                network_json JSON NOT NULL,
                access_policy_json JSON NOT NULL,
                metadata_json JSON NOT NULL,
                last_seen_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                revoked_at DATETIME,
                CONSTRAINT pk_devices PRIMARY KEY (device_id),
                CONSTRAINT fk_devices_owner_id_owners
                    FOREIGN KEY(owner_id) REFERENCES owners (owner_id) ON DELETE CASCADE,
                CONSTRAINT fk_devices_owner_bound_companion
                    FOREIGN KEY(owner_id, bound_companion_id)
                    REFERENCES companions (owner_id, companion_id),
                CONSTRAINT uq_devices_owner_device
                    UNIQUE(owner_id, device_id),
                CONSTRAINT uq_devices_owner_device_bound_companion
                    UNIQUE(owner_id, device_id, bound_companion_id)
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            INSERT INTO devices (
                device_id, owner_id, name, kind, status, approved_at, approved_by,
                bound_companion_id, interaction_mode, auth_type, secret_ref,
                capabilities_json, network_json, access_policy_json, metadata_json,
                last_seen_at, created_at, updated_at, revoked_at
            )
            SELECT
                device_id, owner_id, name, kind, status, approved_at, approved_by,
                bound_companion_id, interaction_mode, auth_type, secret_ref,
                capabilities_json, network_json, access_policy_json, metadata_json,
                last_seen_at, created_at, updated_at, revoked_at
            FROM devices_owner_required
            """
        )
    )
    await conn.execute(text("DROP TABLE devices_owner_required"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_devices_bound_companion_id ON devices (bound_companion_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_devices_kind ON devices (kind)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_devices_owner_id ON devices (owner_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_devices_status ON devices (status)"))


async def _sqlite_rebuild_conversations_if_needed(conn) -> None:
    rows = list((await conn.execute(text("PRAGMA table_info(conversations)"))).mappings())
    if not rows:
        return
    needs_owner_companion_fk = not await _sqlite_has_foreign_key(
        conn,
        "conversations",
        from_columns=("owner_id", "companion_id"),
        to_table="companions",
        to_columns=("owner_id", "companion_id"),
    )
    needs_device_companion_fk = not await _sqlite_has_foreign_key(
        conn,
        "conversations",
        from_columns=("owner_id", "device_id", "companion_id"),
        to_table="devices",
        to_columns=("owner_id", "device_id", "bound_companion_id"),
    )
    if not (needs_owner_companion_fk or needs_device_companion_fk):
        return

    await conn.execute(text("ALTER TABLE conversations RENAME TO conversations_unconstrained"))
    await conn.execute(
        text(
            """
            CREATE TABLE conversations (
                conversation_id VARCHAR(64) NOT NULL,
                owner_id VARCHAR(64) NOT NULL,
                companion_id VARCHAR(64) NOT NULL,
                device_id VARCHAR(128),
                title VARCHAR(256),
                status VARCHAR(32) NOT NULL,
                started_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                ended_at DATETIME,
                metadata_json JSON NOT NULL,
                CONSTRAINT pk_conversations PRIMARY KEY (conversation_id),
                CONSTRAINT fk_conversations_owner_id_owners
                    FOREIGN KEY(owner_id) REFERENCES owners (owner_id) ON DELETE CASCADE,
                CONSTRAINT fk_conversations_companion_id_companions
                    FOREIGN KEY(companion_id) REFERENCES companions (companion_id) ON DELETE CASCADE,
                CONSTRAINT fk_conversations_owner_companion
                    FOREIGN KEY(owner_id, companion_id)
                    REFERENCES companions (owner_id, companion_id) ON DELETE CASCADE,
                CONSTRAINT fk_conversations_owner_device_companion
                    FOREIGN KEY(owner_id, device_id, companion_id)
                    REFERENCES devices (owner_id, device_id, bound_companion_id)
            )
            """
        )
    )
    await conn.execute(
        text(
            """
            INSERT INTO conversations (
                conversation_id, owner_id, companion_id, device_id, title, status,
                started_at, updated_at, ended_at, metadata_json
            )
            SELECT
                conversation_id, owner_id, companion_id, device_id, title, status,
                started_at, COALESCE(updated_at, started_at, CURRENT_TIMESTAMP),
                ended_at, metadata_json
            FROM conversations_unconstrained
            """
        )
    )
    await conn.execute(text("DROP TABLE conversations_unconstrained"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_conversations_companion_id ON conversations (companion_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_conversations_device_id ON conversations (device_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_conversations_owner_id ON conversations (owner_id)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_conversations_owner_started ON conversations (owner_id, started_at)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_conversations_owner_updated ON conversations (owner_id, updated_at)"))
    await conn.execute(text("CREATE INDEX IF NOT EXISTS ix_conversations_status ON conversations (status)"))


async def _sqlite_has_foreign_key(
    conn,
    table_name: str,
    *,
    from_columns: tuple[str, ...],
    to_table: str,
    to_columns: tuple[str, ...],
) -> bool:
    rows = list((await conn.execute(text(f"PRAGMA foreign_key_list({table_name})"))).mappings())
    grouped: dict[int, list[dict]] = {}
    for row in rows:
        grouped.setdefault(int(row["id"]), []).append(dict(row))
    for group in grouped.values():
        ordered = sorted(group, key=lambda row: int(row["seq"]))
        if str(ordered[0]["table"]) != to_table:
            continue
        if tuple(str(row["from"]) for row in ordered) != from_columns:
            continue
        if tuple(str(row["to"]) for row in ordered) != to_columns:
            continue
        return True
    return False


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
