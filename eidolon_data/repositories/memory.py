"""Read-only catalog queries for Memory-owned realms."""

from __future__ import annotations

from sqlalchemy import select

from eidolon_data.repositories.base import Repository
from eidolon_data.schema import MemoryRealmRow


class MemoryRealmsRepository(Repository):
    async def get(self, realm_id: str) -> MemoryRealmRow | None:
        async with self._session_factory() as session:
            return await session.get(MemoryRealmRow, realm_id)

    async def list_for_owner(self, owner_id: str) -> list[MemoryRealmRow]:
        async with self._session_factory() as session:
            return list(
                await session.scalars(
                    select(MemoryRealmRow)
                    .where(MemoryRealmRow.owner_id == owner_id)
                    .order_by(MemoryRealmRow.created_at)
                )
            )
