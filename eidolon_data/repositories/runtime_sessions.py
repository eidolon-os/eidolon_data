"""Runtime session repository."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from eidolon_data.db.base import utc_now
from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import RuntimeSessionRow


class RuntimeSessionsRepository(Repository):
    async def upsert_session(
        self,
        *,
        session_id: str,
        owner_id: str,
        runtime_caller_id: str | None = None,
        companion_id: str | None = None,
        source_device_id: str | None = None,
        transport: str = "",
        status: str = "active",
        metadata_json: dict[str, Any] | None = None,
        seen_at: datetime | None = None,
    ) -> RuntimeSessionRow:
        now = seen_at or utc_now()
        async with self._session_factory() as session:
            row = await session.get(RuntimeSessionRow, session_id)
            if row is None:
                row = RuntimeSessionRow(
                    session_id=session_id,
                    owner_id=owner_id,
                    runtime_caller_id=runtime_caller_id,
                    companion_id=companion_id,
                    source_device_id=source_device_id,
                    started_at=now,
                )
                session.add(row)
            row.owner_id = owner_id
            row.runtime_caller_id = runtime_caller_id
            row.companion_id = companion_id
            row.source_device_id = source_device_id
            row.transport = transport
            row.status = status
            row.metadata_json = metadata_json or {}
            row.last_seen_at = now
            row.updated_at = now
            if status in {"ended", "closed"} and row.ended_at is None:
                row.ended_at = now
            await session.commit()
            await session.refresh(row)
            return row

    async def get(self, session_id: str) -> RuntimeSessionRow | None:
        async with self._session_factory() as session:
            return await session.get(RuntimeSessionRow, session_id)

    async def list_for_caller(
        self,
        runtime_caller_id: str,
        *,
        limit: int = 100,
    ) -> list[RuntimeSessionRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(RuntimeSessionRow)
                .where(RuntimeSessionRow.runtime_caller_id == runtime_caller_id)
                .order_by(RuntimeSessionRow.last_seen_at.desc())
                .limit(limit)
            )
            return list(rows)

    async def list_for_owner(self, owner_id: str, *, limit: int = 100) -> list[RuntimeSessionRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(RuntimeSessionRow)
                .where(RuntimeSessionRow.owner_id == owner_id)
                .order_by(RuntimeSessionRow.last_seen_at.desc())
                .limit(limit)
            )
            return list(rows)
