"""Read-only persistence queries for Owner authority rows."""

from __future__ import annotations

from sqlalchemy import select

from eidolon_data.repositories.base import Repository
from eidolon_data.schema import OwnerRow


class OwnersRepository(Repository):
    async def get(self, owner_id: str) -> OwnerRow | None:
        async with self._session_factory() as session:
            return await session.get(OwnerRow, owner_id)

    async def list(self) -> list[OwnerRow]:
        async with self._session_factory() as session:
            return list(await session.scalars(select(OwnerRow).order_by(OwnerRow.created_at)))
