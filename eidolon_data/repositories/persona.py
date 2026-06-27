"""Persona genome repositories."""

from __future__ import annotations

from sqlalchemy import desc, select

from eidolon_data.db.base import utc_now
from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import CompanionRow, PersonaGenomeRow


class PersonaGenomeConflict(RuntimeError):
    def __init__(self, message: str, *, stale_genome_id: str | None = None) -> None:
        super().__init__(message)
        self.stale_genome_id = stale_genome_id


class PersonaRepository(Repository):
    async def create_genome(
        self,
        *,
        genome_id: str,
        companion_id: str,
        version: int = 1,
        status: str = "committed",
        base_genome_id: str | None = None,
        source_json: dict | None = None,
        genome_json: dict | None = None,
        prompt_markdown: str = "",
        evolution_state_json: dict | None = None,
        change_summary: str = "",
    ) -> PersonaGenomeRow:
        row = PersonaGenomeRow(
            genome_id=genome_id,
            companion_id=companion_id,
            version=version,
            status=status,
            base_genome_id=base_genome_id,
            source_json=source_json or {},
            genome_json=genome_json or {},
            prompt_markdown=prompt_markdown,
            evolution_state_json=evolution_state_json or {},
            change_summary=change_summary,
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
            companion = await session.get(CompanionRow, companion_id)
            if companion is not None and companion.current_genome_id:
                row = await session.get(PersonaGenomeRow, companion.current_genome_id)
                if row is not None:
                    return row
            rows = await session.scalars(
                select(PersonaGenomeRow)
                .where(PersonaGenomeRow.companion_id == companion_id)
                .where(PersonaGenomeRow.status == "committed")
                .order_by(desc(PersonaGenomeRow.version))
                .limit(1)
            )
            row = rows.first()
            if row is not None:
                return row
            rows = await session.scalars(
                select(PersonaGenomeRow)
                .where(PersonaGenomeRow.companion_id == companion_id)
                .order_by(desc(PersonaGenomeRow.version))
                .limit(1)
            )
            return rows.first()

    async def list_for_companion(self, companion_id: str) -> list[PersonaGenomeRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(PersonaGenomeRow)
                .where(PersonaGenomeRow.companion_id == companion_id)
                .order_by(PersonaGenomeRow.version, PersonaGenomeRow.created_at)
            )
            return list(rows)

    async def list_for_companions(self, companion_ids: list[str]) -> list[PersonaGenomeRow]:
        if not companion_ids:
            return []
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(PersonaGenomeRow)
                .where(PersonaGenomeRow.companion_id.in_(companion_ids))
                .order_by(PersonaGenomeRow.companion_id, PersonaGenomeRow.version, PersonaGenomeRow.created_at)
            )
            return list(rows)

    async def create_proposal(
        self,
        *,
        genome_id: str,
        companion_id: str,
        base_genome_id: str,
        source_json: dict | None = None,
        genome_json: dict | None = None,
        prompt_markdown: str = "",
        evolution_state_json: dict | None = None,
        change_summary: str = "",
    ) -> PersonaGenomeRow:
        async with self._session_factory() as session:
            max_version = (
                await session.execute(
                    select(PersonaGenomeRow.version)
                    .where(PersonaGenomeRow.companion_id == companion_id)
                    .order_by(desc(PersonaGenomeRow.version))
                    .limit(1)
                )
            ).scalar_one_or_none()
            row = PersonaGenomeRow(
                genome_id=genome_id,
                companion_id=companion_id,
                version=(max_version or 0) + 1,
                status="proposed",
                base_genome_id=base_genome_id,
                source_json=source_json or {},
                genome_json=genome_json or {},
                prompt_markdown=prompt_markdown,
                evolution_state_json=evolution_state_json or {},
                change_summary=change_summary,
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def activate_genome(
        self,
        *,
        companion_id: str,
        genome_id: str,
        expected_base_genome_id: str | None = None,
    ) -> PersonaGenomeRow:
        async with self._session_factory() as session:
            companion = await session.get(CompanionRow, companion_id)
            genome = await session.get(PersonaGenomeRow, genome_id)
            if companion is None:
                raise KeyError(f"companion not found: {companion_id}")
            if genome is None or genome.companion_id != companion_id:
                raise KeyError(f"genome not found: {genome_id}")
            if expected_base_genome_id and companion.current_genome_id != expected_base_genome_id:
                genome.status = "stale"
                genome.updated_at = utc_now()
                await session.commit()
                raise PersonaGenomeConflict(
                    "current genome changed before activation",
                    stale_genome_id=genome_id,
                )
            genome.status = "committed"
            genome.updated_at = utc_now()
            companion.current_genome_id = genome_id
            companion.updated_at = utc_now()
            await session.commit()
            await session.refresh(genome)
            return genome

    async def reject_genome(self, genome_id: str, *, reason: str = "") -> PersonaGenomeRow:
        return await self._set_status(genome_id, status="rejected", reason=reason)

    async def mark_stale(self, genome_id: str, *, reason: str = "") -> PersonaGenomeRow:
        return await self._set_status(genome_id, status="stale", reason=reason)

    async def rollback_to_genome(self, *, companion_id: str, genome_id: str) -> PersonaGenomeRow:
        async with self._session_factory() as session:
            companion = await session.get(CompanionRow, companion_id)
            genome = await session.get(PersonaGenomeRow, genome_id)
            if companion is None:
                raise KeyError(f"companion not found: {companion_id}")
            if genome is None or genome.companion_id != companion_id:
                raise KeyError(f"genome not found: {genome_id}")
            if genome.status != "committed":
                raise ValueError("only committed genomes can be rollback targets")
            companion.current_genome_id = genome_id
            companion.updated_at = utc_now()
            await session.commit()
            await session.refresh(genome)
            return genome

    async def _set_status(self, genome_id: str, *, status: str, reason: str = "") -> PersonaGenomeRow:
        async with self._session_factory() as session:
            row = await session.get(PersonaGenomeRow, genome_id)
            if row is None:
                raise KeyError(f"genome not found: {genome_id}")
            row.status = status
            if reason:
                row.change_summary = f"{row.change_summary}\n\n{reason}".strip()
            row.updated_at = utc_now()
            await session.commit()
            await session.refresh(row)
            return row
