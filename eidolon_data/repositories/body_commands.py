"""Body command audit repository."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from eidolon_data.db.base import utc_now
from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import BodyCommandRow


class BodyCommandsRepository(Repository):
    async def upsert_command(
        self,
        *,
        command_id: str,
        device_id: str,
        owner_id: str | None = None,
        companion_id: str | None = None,
        runtime_caller_id: str | None = None,
        runtime_session_id: str | None = None,
        source_device_id: str | None = None,
        topic: str = "",
        op: str = "",
        status: str = "queued",
        payload_json: dict[str, Any] | None = None,
        envelope_json: dict[str, Any] | None = None,
        ack_json: dict[str, Any] | None = None,
        result_json: dict[str, Any] | None = None,
        ttl_ms: int = 30_000,
        qos: str = "ack",
        priority: str = "normal",
        error: str = "",
        created_at: datetime | None = None,
        updated_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> BodyCommandRow:
        now = updated_at or utc_now()
        async with self._session_factory() as session:
            row = await session.get(BodyCommandRow, command_id)
            if row is None:
                row = BodyCommandRow(
                    command_id=command_id,
                    device_id=device_id,
                    created_at=created_at or now,
                )
                session.add(row)

            row.owner_id = owner_id
            row.companion_id = companion_id
            row.runtime_caller_id = runtime_caller_id
            row.runtime_session_id = runtime_session_id
            row.device_id = device_id
            row.source_device_id = source_device_id
            row.topic = topic
            row.op = op
            row.status = status
            row.payload_json = payload_json or {}
            row.envelope_json = envelope_json or {}
            row.ack_json = ack_json
            row.result_json = result_json
            row.ttl_ms = ttl_ms
            row.qos = qos
            row.priority = priority
            row.error = error
            row.updated_at = now
            row.expires_at = expires_at
            await session.commit()
            await session.refresh(row)
            return row

    async def update_status(
        self,
        command_id: str,
        *,
        status: str,
        error: str = "",
        ack_json: dict[str, Any] | None = None,
        result_json: dict[str, Any] | None = None,
        updated_at: datetime | None = None,
    ) -> BodyCommandRow | None:
        async with self._session_factory() as session:
            row = await session.get(BodyCommandRow, command_id)
            if row is None:
                return None
            row.status = status
            row.error = error
            row.ack_json = ack_json
            row.result_json = result_json
            row.updated_at = updated_at or utc_now()
            await session.commit()
            await session.refresh(row)
            return row

    async def get_command(self, command_id: str) -> BodyCommandRow | None:
        async with self._session_factory() as session:
            return await session.get(BodyCommandRow, command_id)

    async def list_recent(self, *, limit: int = 50) -> list[BodyCommandRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(BodyCommandRow).order_by(BodyCommandRow.created_at.desc()).limit(limit)
            )
            return list(rows)

    async def list_for_device(self, device_id: str, *, limit: int = 50) -> list[BodyCommandRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(BodyCommandRow)
                .where(BodyCommandRow.device_id == device_id)
                .order_by(BodyCommandRow.created_at.desc())
                .limit(limit)
            )
            return list(rows)
