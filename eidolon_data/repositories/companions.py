"""Persistence for Companion authority rows.

Reads plus the one thing an Owner may change about a Companion directly: what
they call it. Everything else about a Companion is decided by the flows that
create and run it.
"""

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

    async def rename(self, companion_id: str, display_name: str) -> CompanionRow | None:
        """Give this Companion the name its Owner chose.

        Returns None when there is no such Companion, so the caller answers
        "which Companion?" rather than reporting a rename that touched nothing.
        """

        async with self._session_factory() as session:
            row = await session.get(CompanionRow, companion_id)
            if row is None:
                return None
            row.display_name = display_name
            await session.commit()
            await session.refresh(row)
            return row

    async def get_primary_for_owner(self, owner_id: str) -> CompanionRow | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(CompanionRow).where(
                    CompanionRow.owner_id == owner_id,
                    CompanionRow.role == "primary",
                    CompanionRow.status == "active",
                )
            )
