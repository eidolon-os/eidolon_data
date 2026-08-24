"""Persistence for Companion authority rows.

Reads plus the one thing an Owner may change about a Companion directly: what
they call it. Everything else about a Companion is decided by the flows that
create and run it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, tuple_

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

    async def page_for_owner(
        self,
        owner_id: str,
        *,
        limit: int,
        after: tuple[datetime, str] | None = None,
    ) -> list[CompanionRow]:
        """One page of this Owner's Companions, oldest first.

        Ordered by creation and then id — never by whether one is the default.
        Which Companion is the default is a single field on the Owner, and if
        the order encoded it too there would be two places saying so and a way
        for them to disagree. A caller that wants the default first has the
        pointer and can put it first.

        ``after`` is the sort key of the last row already seen, so a page
        boundary lands between two rows rather than at an offset that shifts
        when a Companion is created.
        """
        async with self._session_factory() as session:
            query = select(CompanionRow).where(CompanionRow.owner_id == owner_id)
            if after is not None:
                created_at, companion_id = after
                query = query.where(
                    tuple_(CompanionRow.created_at, CompanionRow.companion_id)
                    > (created_at, companion_id)
                )
            query = query.order_by(
                CompanionRow.created_at, CompanionRow.companion_id
            ).limit(limit)
            return list(await session.scalars(query))

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
