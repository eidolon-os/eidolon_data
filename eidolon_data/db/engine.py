"""Engine/session construction for Eidolon Data."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from eidolon_data.db.base import Base
from eidolon_data.settings import DataSettings


def create_engine(settings: DataSettings) -> AsyncEngine:
    engine_kwargs: dict[str, object] = {"echo": settings.echo_sql}
    if settings.database_url.startswith("sqlite+aiosqlite:///"):
        if not settings.sqlite_read_only:
            Path(settings.sqlite_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
        engine_kwargs.update(
            connect_args={"timeout": settings.sqlite_busy_timeout_ms / 1_000},
            pool_size=settings.sqlite_pool_size,
            max_overflow=0,
        )
    engine = create_async_engine(settings.database_url, **engine_kwargs)
    if settings.database_url.startswith("sqlite+aiosqlite:///"):
        _install_sqlite_profile(engine, settings)
    return engine


def _install_sqlite_profile(engine: AsyncEngine, settings: DataSettings) -> None:
    """Apply the authority-store SQLite profile to every DB-API connection.

    ``journal_mode`` persists in the database file; the remaining PRAGMAs are
    connection-local and therefore must not be left to sqlite/driver defaults.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _configure_sqlite(
        dbapi_connection: Any,
        _connection_record: object,
    ) -> None:
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute(f"PRAGMA busy_timeout={settings.sqlite_busy_timeout_ms}")
            if settings.sqlite_read_only:
                cursor.execute("PRAGMA query_only=ON")
            else:
                cursor.execute(f"PRAGMA journal_mode={settings.sqlite_journal_mode}")
                cursor.execute(f"PRAGMA synchronous={settings.sqlite_synchronous}")
                cursor.execute(
                    f"PRAGMA wal_autocheckpoint={settings.sqlite_wal_autocheckpoint_pages}"
                )
        finally:
            cursor.close()


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def init_schema(engine: AsyncEngine) -> None:
    """Create the current schema for tests and isolated development stores."""
    import eidolon_data.schema  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_assert_canonical_schema)


async def validate_schema(engine: AsyncEngine) -> None:
    """Fail closed on a non-canonical production schema without repairing it."""
    import eidolon_data.schema  # noqa: F401

    async with engine.connect() as conn:
        await conn.run_sync(_assert_canonical_schema)


_RETIRED_SYSTEM_TABLES = {
    "devices",
    "body_commands",
    "guard_policy_actions",
    "guard_runtime_deliveries",
    "guard_owner_face_profile_deliveries",
    "runtime_sessions",
    "conversations",
    "turns",
    "messages",
    "jobs",
    "events",
}


def _assert_canonical_schema(connection: Connection) -> None:
    """Reject drift, missing V2 tables, and every retired ownership surface."""

    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    expected_tables = set(Base.metadata.tables)
    problems: list[str] = []
    retired = sorted(tables & _RETIRED_SYSTEM_TABLES)
    if retired:
        problems.append("retired runtime/event tables remain in System Data: " + ", ".join(retired))
    missing_tables = sorted(expected_tables - tables)
    if missing_tables:
        problems.append("missing canonical tables: " + ", ".join(missing_tables))
    extra_tables = sorted(tables - expected_tables - {"alembic_version"})
    if extra_tables:
        problems.append("unexpected tables: " + ", ".join(extra_tables))
    for table_name in sorted(expected_tables & tables):
        expected = set(Base.metadata.tables[table_name].columns.keys())
        actual = {column["name"] for column in inspector.get_columns(table_name)}
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        if missing:
            problems.append(f"{table_name} missing columns {', '.join(missing)}")
        if unexpected:
            problems.append(f"{table_name} has unexpected columns {', '.join(unexpected)}")
    if problems:
        raise RuntimeError("non-canonical System Data schema: " + "; ".join(problems))
