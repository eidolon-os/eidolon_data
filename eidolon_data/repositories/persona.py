"""Read-only persistence queries for immutable Persona Genome snapshots."""

from __future__ import annotations

from sqlalchemy import select

from eidolon_data.repositories.base import Repository
from eidolon_data.schema import CompanionRow, PersonaGenomeRow


class PersonaGenomeConflict(RuntimeError):
    def __init__(self, message: str, *, stale_genome_id: str | None = None) -> None:
        super().__init__(message)
        self.stale_genome_id = stale_genome_id


class PersonaRepository(Repository):
    async def get(self, genome_id: str) -> PersonaGenomeRow | None:
        async with self._session_factory() as session:
            return await session.get(PersonaGenomeRow, genome_id)

    async def get_current(self, companion_id: str) -> PersonaGenomeRow | None:
        async with self._session_factory() as session:
            companion = await session.get(CompanionRow, companion_id)
            if companion is None or not companion.current_genome_id:
                return None
            return await session.get(PersonaGenomeRow, companion.current_genome_id)

    async def list_for_companion(self, companion_id: str) -> list[PersonaGenomeRow]:
        async with self._session_factory() as session:
            return list(
                await session.scalars(
                    select(PersonaGenomeRow)
                    .where(PersonaGenomeRow.companion_id == companion_id)
                    .order_by(PersonaGenomeRow.version)
                )
            )
