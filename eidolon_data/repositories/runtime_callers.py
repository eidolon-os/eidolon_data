"""Runtime caller repository."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select

from eidolon_data.db.base import utc_now
from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import RuntimeCallerRow


class RuntimeCallersRepository(Repository):
    async def upsert_caller(
        self,
        *,
        caller_id: str,
        owner_id: str,
        actor_kind: str,
        actor_id: str,
        companion_id: str | None = None,
        display_name: str = "",
        source_device_id: str | None = None,
        status: str = "active",
        metadata_json: dict[str, Any] | None = None,
        seen_at: datetime | None = None,
    ) -> RuntimeCallerRow:
        now = seen_at or utc_now()
        async with self._session_factory() as session:
            row = await session.get(RuntimeCallerRow, caller_id)
            if row is None:
                row = RuntimeCallerRow(
                    caller_id=caller_id,
                    owner_id=owner_id,
                    actor_kind=actor_kind,
                    actor_id=actor_id,
                    companion_id=companion_id,
                    first_seen_at=now,
                )
                session.add(row)
            row.owner_id = owner_id
            row.companion_id = companion_id
            row.actor_kind = actor_kind
            row.actor_id = actor_id
            row.display_name = display_name
            row.source_device_id = source_device_id
            row.status = status
            row.metadata_json = metadata_json or {}
            row.last_seen_at = now
            row.updated_at = now
            await session.commit()
            await session.refresh(row)
            return row

    async def get(self, caller_id: str) -> RuntimeCallerRow | None:
        async with self._session_factory() as session:
            return await session.get(RuntimeCallerRow, caller_id)

    async def list_for_owner(self, owner_id: str, *, limit: int = 100) -> list[RuntimeCallerRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(RuntimeCallerRow)
                .where(RuntimeCallerRow.owner_id == owner_id)
                .order_by(RuntimeCallerRow.last_seen_at.desc())
                .limit(limit)
            )
            return list(rows)
