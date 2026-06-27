"""Owner repository."""

from __future__ import annotations

from sqlalchemy import select

from eidolon_data.db.base import utc_now
from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import OwnerRow


class OwnersRepository(Repository):
    async def create(
        self,
        *,
        owner_id: str,
        display_name: str = "",
        kind: str = "person",
        status: str = "active",
        profile_json: dict | None = None,
        settings_json: dict | None = None,
    ) -> OwnerRow:
        row = OwnerRow(
            owner_id=owner_id,
            display_name=display_name,
            kind=kind,
            status=status,
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

    async def update(
        self,
        owner_id: str,
        *,
        display_name: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        profile_json: dict | None = None,
        settings_json: dict | None = None,
    ) -> OwnerRow:
        async with self._session_factory() as session:
            row = await session.get(OwnerRow, owner_id)
            if row is None:
                raise KeyError(f"owner not found: {owner_id}")
            if display_name is not None:
                row.display_name = display_name
            if kind is not None:
                row.kind = kind
            if status is not None:
                row.status = status
            if profile_json is not None:
                row.profile_json = profile_json
            if settings_json is not None:
                row.settings_json = settings_json
            row.updated_at = utc_now()
            await session.commit()
            await session.refresh(row)
            return row

    async def archive(self, owner_id: str) -> OwnerRow:
        return await self.update(owner_id, status="archived")
