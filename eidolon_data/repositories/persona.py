"""Persona genome repositories."""

from __future__ import annotations

from sqlalchemy import desc, select

from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import PersonaGenomeRow


class PersonaRepository(Repository):
    async def create_genome(
        self,
        *,
        genome_id: str,
        companion_id: str,
        version: int = 1,
        source_json: dict | None = None,
        genome_json: dict | None = None,
        evolution_state_json: dict | None = None,
    ) -> PersonaGenomeRow:
        row = PersonaGenomeRow(
            genome_id=genome_id,
            companion_id=companion_id,
            version=version,
            source_json=source_json or {},
            genome_json=genome_json or {},
            evolution_state_json=evolution_state_json or {},
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def get_genome(self, genome_id: str) -> PersonaGenomeRow | None:
        async with self._session_factory() as session:
            return await session.get(PersonaGenomeRow, genome_id)

    async def get_current_genome(self, companion_id: str) -> PersonaGenomeRow | None:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(PersonaGenomeRow)
                .where(PersonaGenomeRow.companion_id == companion_id)
                .order_by(desc(PersonaGenomeRow.version))
                .limit(1)
            )
            return rows.first()
