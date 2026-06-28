"""Memory sovereignty repositories."""

from __future__ import annotations

from sqlalchemy import select

from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import CompanionRow, MemoryRealmRow


class MemoryRepository(Repository):
    async def create_realm(
        self,
        *,
        realm_id: str,
        owner_id: str,
        companion_id: str,
        engine: str = "mempalace",
        engine_config_json: dict | None = None,
        policy_json: dict | None = None,
        status: str = "active",
    ) -> MemoryRealmRow:
        async with self._session_factory() as session:
            companion = await session.get(CompanionRow, companion_id)
            if companion is None:
                raise KeyError(f"companion not found: {companion_id}")
            if companion.owner_id != owner_id:
                raise ValueError(
                    f"companion {companion_id!r} belongs to owner {companion.owner_id!r}, not {owner_id!r}"
                )
            row = MemoryRealmRow(
                realm_id=realm_id,
                owner_id=owner_id,
                companion_id=companion_id,
                engine=engine,
                engine_config_json=engine_config_json or {},
                policy_json=policy_json or {},
                status=status,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def get_realm(self, realm_id: str) -> MemoryRealmRow | None:
        async with self._session_factory() as session:
            return await session.get(MemoryRealmRow, realm_id)

    async def list_realms_for_owner(self, owner_id: str) -> list[MemoryRealmRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(MemoryRealmRow)
                .where(MemoryRealmRow.owner_id == owner_id)
                .order_by(MemoryRealmRow.created_at)
            )
            return list(rows)
