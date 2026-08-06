"""Read-only persistence queries for Companion authority rows."""

from __future__ import annotations

from sqlalchemy import select

from eidolon_data.repositories.base import Repository
from eidolon_data.schema import CompanionRow


class CompanionsRepository(Repository):
    async def get(self, companion_id: str) -> CompanionRow | None:
        async with self._session_factory() as session:
            return await session.get(CompanionRow, companion_id)

    async def list_for_owner(self, owner_id: str) -> list[CompanionRow]:
        async with self._session_factory() as session:
            return list(
                await session.scalars(
                    select(CompanionRow)
                    .where(CompanionRow.owner_id == owner_id)
                    .order_by(CompanionRow.created_at)
                )
            )

    async def get_primary_for_owner(self, owner_id: str) -> CompanionRow | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(CompanionRow).where(
                    CompanionRow.owner_id == owner_id,
                    CompanionRow.role == "primary",
                    CompanionRow.status == "active",
                )
            )
