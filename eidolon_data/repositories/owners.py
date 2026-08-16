"""Persistence queries for Owner authority rows."""

from __future__ import annotations

from sqlalchemy import select

from eidolon_data.repositories.base import Repository
from eidolon_data.schema import OwnerRow
from eidolon_data.db.base import utc_now


class OwnersRepository(Repository):
    async def get(self, owner_id: str) -> OwnerRow | None:
        async with self._session_factory() as session:
            return await session.get(OwnerRow, owner_id)

    async def list(self) -> list[OwnerRow]:
        async with self._session_factory() as session:
            return list(await session.scalars(select(OwnerRow).order_by(OwnerRow.created_at)))

    async def rename(self, owner_id: str, display_name: str) -> OwnerRow | None:
        """Set what this Owner is called.

        The name is the person's own, given at first use and never editable
        since — the Eidolon greets them by it on every screen. Returns None
        when there is no such Owner, so the caller answers "which Owner?"
        rather than reporting a rename that touched nothing.
        """

        async with self._session_factory() as session:
            row = await session.get(OwnerRow, owner_id)
            if row is None:
                return None
            row.display_name = display_name
            row.updated_at = utc_now()
            await session.commit()
            await session.refresh(row)
            return row
