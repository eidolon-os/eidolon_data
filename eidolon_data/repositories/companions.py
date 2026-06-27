"""Companion repository."""

from __future__ import annotations

from sqlalchemy import select

from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import CompanionRow


class CompanionsRepository(Repository):
    async def create(
        self,
        *,
        companion_id: str,
        owner_id: str,
        display_name: str = "",
        kind: str = "companion",
        status: str = "active",
        profile_json: dict | None = None,
        runtime_config_json: dict | None = None,
        metadata_json: dict | None = None,
    ) -> CompanionRow:
        row = CompanionRow(
            companion_id=companion_id,
            owner_id=owner_id,
            display_name=display_name,
            kind=kind,
            status=status,
            profile_json=profile_json or {},
            runtime_config_json=runtime_config_json or {},
            metadata_json=metadata_json or {},
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def get(self, companion_id: str) -> CompanionRow | None:
        async with self._session_factory() as session:
            return await session.get(CompanionRow, companion_id)

    async def list_for_owner(self, owner_id: str) -> list[CompanionRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(CompanionRow)
                .where(CompanionRow.owner_id == owner_id)
                .order_by(CompanionRow.created_at)
            )
            return list(rows)

    async def set_current_genome(self, companion_id: str, genome_id: str) -> None:
        async with self._session_factory() as session:
            row = await session.get(CompanionRow, companion_id)
            if row is None:
                raise KeyError(f"companion not found: {companion_id}")
            row.current_genome_id = genome_id
            await session.commit()

    async def set_default_memory_realm(self, companion_id: str, realm_id: str) -> None:
        async with self._session_factory() as session:
            row = await session.get(CompanionRow, companion_id)
            if row is None:
                raise KeyError(f"companion not found: {companion_id}")
            row.default_memory_realm_id = realm_id
            await session.commit()

