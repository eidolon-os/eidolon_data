"""Owner repository."""

from __future__ import annotations

from sqlalchemy import select

from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import OwnerRow


class OwnersRepository(Repository):
    async def create(
        self,
        *,
        owner_id: str,
        display_name: str = "",
        kind: str = "person",
        profile_json: dict | None = None,
        settings_json: dict | None = None,
    ) -> OwnerRow:
        row = OwnerRow(
            owner_id=owner_id,
            display_name=display_name,
            kind=kind,
            profile_json=profile_json or {},
            settings_json=settings_json or {},
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def get(self, owner_id: str) -> OwnerRow | None:
        async with self._session_factory() as session:
            return await session.get(OwnerRow, owner_id)

    async def list(self) -> list[OwnerRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(select(OwnerRow).order_by(OwnerRow.created_at))
            return list(rows)

