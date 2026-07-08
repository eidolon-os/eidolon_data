"""Companion repository."""

from __future__ import annotations

from sqlalchemy import select

from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import CompanionRow, MemoryRealmRow, PersonaGenomeRow


class CompanionsRepository(Repository):
    async def create(
        self,
        *,
        companion_id: str,
        owner_id: str,
        display_name: str = "",
        kind: str = "companion",
        status: str = "active",
        is_master: bool = False,
        companion_type: str | None = None,
        profile_json: dict | None = None,
        runtime_config_json: dict | None = None,
        metadata_json: dict | None = None,
    ) -> CompanionRow:
        resolved_companion_type = companion_type or ("master" if is_master else "slave")
        if resolved_companion_type not in {"master", "slave"}:
            raise ValueError("companion_type must be master or slave")
        resolved_is_master = resolved_companion_type == "master"
        row = CompanionRow(
            companion_id=companion_id,
            owner_id=owner_id,
            display_name=display_name,
            kind=kind,
            status=status,
            is_master=resolved_is_master,
            companion_type=resolved_companion_type,
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
            genome = await session.get(PersonaGenomeRow, genome_id)
            if genome is None:
                raise KeyError(f"genome not found: {genome_id}")
            if genome.companion_id != companion_id:
                raise ValueError(
                    f"genome {genome_id!r} belongs to companion {genome.companion_id!r}, not {companion_id!r}"
                )
            row.current_genome_id = genome_id
            await session.commit()

    async def set_default_memory_realm(self, companion_id: str, realm_id: str) -> None:
        async with self._session_factory() as session:
            row = await session.get(CompanionRow, companion_id)
            if row is None:
                raise KeyError(f"companion not found: {companion_id}")
            realm = await session.get(MemoryRealmRow, realm_id)
            if realm is None:
                raise KeyError(f"memory realm not found: {realm_id}")
            if realm.companion_id != companion_id:
                raise ValueError(
                    f"memory realm {realm_id!r} belongs to companion {realm.companion_id!r}, not {companion_id!r}"
                )
            if realm.owner_id != row.owner_id:
                raise ValueError(
                    f"memory realm {realm_id!r} belongs to owner {realm.owner_id!r}, not {row.owner_id!r}"
                )
            row.default_memory_realm_id = realm_id
            await session.commit()
