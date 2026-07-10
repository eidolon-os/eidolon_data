"""Engine/session construction for Eidolon Data."""

from __future__ import annotations

import logging
from pathlib import Path

from sqlalchemy import Connection, text
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.schema import Column, Table

from eidolon_data.db.base import Base
from eidolon_data.settings import DataSettings

_log = logging.getLogger(__name__)


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
        await conn.run_sync(_reconcile_schema_sync, Base.metadata)
        await conn.execute(
            text(
                """
                CREATE TRIGGER IF NOT EXISTS trg_persona_genomes_immutable
                BEFORE UPDATE OF companion_id, version, base_genome_id, schema_version,
                                 genome_hash, realizer_version, genome_json
                ON persona_genomes
                BEGIN
                    SELECT RAISE(ABORT, 'persona genome snapshots are immutable');
                END
                """
            )
        )


# ---------------------------------------------------------------------------
# Additive schema reconcile
# ---------------------------------------------------------------------------
# ``create_all`` only creates *missing tables*; it never adds a column to a
# table that already exists. On a long-lived dev SQLite DB (built by create_all,
# no Alembic stamp) that means every new model column silently drifts and every
# read of the affected table 500s (e.g. ``no such column: turns.trace_id``).
#
# ``reconcile_schema`` closes that gap for the SAFE, additive case: it diffs the
# ORM metadata against the live table columns and ``ALTER TABLE ADD COLUMN`` the
# ones that can be added online. It is deliberately conservative and NOT a
# migration engine:
#   * only ADDs columns (never drops/renames/retypes),
#   * only when the column is nullable, or has a server_default, or a scalar
#     python default we can render as a DB default,
#   * skips UNIQUE columns and NOT-NULL-without-default columns (they need a
#     backfill / index → an Alembic migration),
# and it WARN-logs every ALTER so schema changes are never silent.
#
# Authoritative history + complex/destructive migrations stay in Alembic; this
# is only the create_all companion that keeps simple additive drift from 500ing.


def _literal(value: object) -> str | None:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return "'" + value.replace("'", "''") + "'"
    return None


def _add_column_ddl(table: Table, column: Column, dialect) -> str | None:
    """Build a safe ``ALTER TABLE ADD COLUMN`` clause, or None if unsafe."""
    # UNIQUE columns can't be added online in SQLite and usually need a backfill
    # (e.g. persona_genomes.genome_hash) — defer to Alembic.
    if column.unique:
        return None

    default_sql: str | None = None
    if column.server_default is not None:
        arg = column.server_default.arg
        default_sql = str(arg.text) if hasattr(arg, "text") else _literal(arg)
    elif column.default is not None and getattr(column.default, "is_scalar", False):
        default_sql = _literal(column.default.arg)

    if not column.nullable and default_sql is None:
        # NOT NULL without a renderable default can't be added to a populated
        # table — needs a migration with a backfill.
        return None

    type_sql = column.type.compile(dialect=dialect)
    parts = [f'"{column.name}"', type_sql]
    if not column.nullable:
        parts.append("NOT NULL")
    if default_sql is not None:
        parts.append(f"DEFAULT {default_sql}")
    return f'ALTER TABLE "{table.name}" ADD COLUMN {" ".join(parts)}'


def _reconcile_schema_sync(conn: Connection, metadata) -> None:
    inspector = sa_inspect(conn)
    existing_tables = set(inspector.get_table_names())
    dialect = conn.dialect

    for table in metadata.sorted_tables:
        if table.name not in existing_tables:
            # create_all just made it (with all columns/indexes) — nothing to do.
            continue
        existing_cols = {col["name"] for col in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in existing_cols:
                continue
            ddl = _add_column_ddl(table, column, dialect)
            if ddl is None:
                _log.warning(
                    "[schema-reconcile] %s.%s missing but not additive-safe "
                    "(unique / not-null without default) — needs an Alembic migration",
                    table.name,
                    column.name,
                )
                continue
            _log.warning("[schema-reconcile] %s", ddl)
            conn.execute(text(ddl))
            # Recreate the column's index if the ORM declares one (perf parity).
            if column.index:
                index_name = f"ix_{table.name}_{column.name}"
                conn.execute(
                    text(
                        f'CREATE INDEX IF NOT EXISTS "{index_name}" '
                        f'ON "{table.name}" ("{column.name}")'
                    )
                )
