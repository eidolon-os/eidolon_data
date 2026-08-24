"""Persistence for Companion authority rows.

Reads plus the one thing an Owner may change about a Companion directly: what
they call it. Everything else about a Companion is decided by the flows that
create and run it.
"""

from __future__ import annotations

from sqlalchemy import select

from eidolon_data.repositories.base import Repository
from eidolon_data.schema import CompanionRow, OwnerRow


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

    async def get_default_for_owner(self, owner_id: str) -> CompanionRow | None:
        """The Companion this Owner's unaddressed requests go to.

        Read through the Owner's pointer rather than by scanning Companions for
        a flag: there is exactly one answer because there is exactly one place
        it is written. A ``lifecycle_state`` check still applies — a pointer at
        an archived Companion is a broken pointer, and answering with it would
        route new sessions into something that refuses them.
        """
        async with self._session_factory() as session:
            owner = await session.get(OwnerRow, owner_id)
            if owner is None or not owner.default_companion_id:
                return None
            companion = await session.get(CompanionRow, owner.default_companion_id)
            if (
                companion is None
                or companion.owner_id != owner_id
                or companion.lifecycle_state != "active"
            ):
                return None
            return companion
