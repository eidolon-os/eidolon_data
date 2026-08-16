"""Persistence for immutable Persona Genome snapshots, and going back to one.

Genomes are never edited. Returning to an earlier one appends a new version
carrying that older content, so the record of what this Companion has been
stays a record — and the act of going back is itself part of it, which is the
part someone will want to see later when they wonder what happened.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func, select

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

    async def restore(
        self,
        *,
        companion_id: str,
        genome_id: str,
        change_summary: str,
    ) -> PersonaGenomeRow:
        """Make this Companion what it was at an earlier genome.

        Appends rather than rewinds. `base_genome_id` points at what was
        restored, so the timeline can say "this is when it went back to being
        the way it was in March" instead of quietly losing the months between.
        """

        async with self._session_factory() as session:
            target = await session.get(PersonaGenomeRow, genome_id)
            if target is None or target.companion_id != companion_id:
                raise PersonaGenomeConflict("persona genome does not belong to companion")
            if target.status != "committed":
                # Only something this Companion actually was may be returned
                # to. A proposal it never became is not a past.
                raise PersonaGenomeConflict(
                    "only a committed persona genome can be restored",
                    stale_genome_id=genome_id,
                )
            companion = await session.get(CompanionRow, companion_id)
            if companion is None:
                raise PersonaGenomeConflict("companion does not exist")
            if companion.current_genome_id == genome_id:
                raise PersonaGenomeConflict(
                    "companion already has this persona genome",
                    stale_genome_id=genome_id,
                )
            highest = await session.scalar(
                select(func.max(PersonaGenomeRow.version)).where(
                    PersonaGenomeRow.companion_id == companion_id
                )
            )
            now = datetime.now(timezone.utc)
            restored = PersonaGenomeRow(
                genome_id=f"g_{uuid4().hex}",
                companion_id=companion_id,
                version=(highest or 0) + 1,
                status="committed",
                base_genome_id=target.genome_id,
                schema_version=target.schema_version,
                genome_hash=target.genome_hash,
                realizer_version=target.realizer_version,
                source_json={
                    "source_type": "owner_restore",
                    "restored_from": target.genome_id,
                    "restored_version": target.version,
                },
                genome_json=dict(target.genome_json or {}),
                change_summary=change_summary,
                created_at=now,
                updated_at=now,
            )
            session.add(restored)
            companion.current_genome_id = restored.genome_id
            await session.commit()
            await session.refresh(restored)
            return restored
