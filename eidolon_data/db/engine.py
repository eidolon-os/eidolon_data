"""Engine/session construction for Eidolon Data."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect
from sqlalchemy.engine import Connection
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
        await conn.run_sync(_assert_guard_schema)


_GUARD_COLUMNS: dict[str, set[str]] = {
    "guard_bindings": {
        "binding_id",
        "owner_id",
        "guard_companion_id",
        "device_id",
        "state",
        "policy_id",
        "config_revision",
        "config_json",
        "runtime_revision",
        "runtime_config_json",
        "desired_runtime_state",
        "status_json",
        "activated_at",
        "disabled_at",
        "revoked_at",
        "created_at",
        "updated_at",
    },
    "guard_policy_actions": {
        "action_id",
        "binding_id",
        "owner_id",
        "guard_companion_id",
        "device_id",
        "correlation_id",
        "guard_epoch",
        "fact_type",
        "replay_key",
        "policy_id",
        "action",
        "subscriber",
        "status",
        "payload_json",
        "ack_json",
        "command_id",
        "delivery_attempt_count",
        "last_error",
        "delivery_claim_token",
        "delivery_lease_expires_at",
        "next_attempt_at",
        "delivery_dead_lettered_at",
        "dispatched_at",
        "published_at",
        "acknowledged_at",
        "created_at",
        "updated_at",
    },
    "guard_runtime_deliveries": {
        "delivery_id",
        "binding_id",
        "owner_id",
        "device_id",
        "runtime_revision",
        "desired_runtime_state",
        "status",
        "command_id",
        "attempt_count",
        "last_error",
        "lease_expires_at",
        "dispatched_at",
        "applied_at",
        "result_json",
        "created_at",
        "updated_at",
    },
    "owner_face_profile_revisions": {
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
    },
    "owner_face_references": {
        "reference_id",
        "profile_revision_id",
        "pose",
        "content_type",
        "size_bytes",
        "sha256",
        "storage_key",
        "created_at",
    },
    "guard_owner_face_profile_deliveries": {
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
    },
}


def _assert_guard_schema(connection: Connection) -> None:
    """Reject legacy Guard storage instead of silently running against it."""

    inspector = inspect(connection)
    tables = set(inspector.get_table_names())
    problems: list[str] = []
    if "storage_objects" in tables:
        problems.append("legacy table storage_objects is present")
    for table, expected in _GUARD_COLUMNS.items():
        if table not in tables:
            problems.append(f"missing table {table}")
            continue
        actual = {column["name"] for column in inspector.get_columns(table)}
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        if missing:
            problems.append(f"{table} missing columns {', '.join(missing)}")
        if unexpected:
            problems.append(f"{table} has unexpected columns {', '.join(unexpected)}")
    if problems:
        raise RuntimeError("non-canonical Guard schema: " + "; ".join(problems))
