"""Read-oriented persistence access for canonical persona snapshots."""

from __future__ import annotations

from eidolon_sdk.biz.persona import (
    PERSONA_GENOME_SCHEMA,
    PERSONA_REALIZER,
    build_default_persona_genome,
    normalize_persona_genome,
    persona_genome_hash,
    persona_genome_to_json,
)
from sqlalchemy import desc, select

from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import CompanionRow, PersonaGenomeRow


class PersonaGenomeConflict(RuntimeError):
    def __init__(self, message: str, *, stale_genome_id: str | None = None) -> None:
        super().__init__(message)
        self.stale_genome_id = stale_genome_id


class PersonaRepository(Repository):
    """Store complete immutable snapshots; lifecycle transitions belong to PersonaService."""

    async def create_genome(
        self,
        *,
        genome_id: str,
        companion_id: str,
        version: int = 1,
        status: str = "committed",
        base_genome_id: str | None = None,
        schema_version: str = PERSONA_GENOME_SCHEMA,
        genome_hash: str | None = None,
        realizer_version: str = PERSONA_REALIZER,
        applied_event_id: str | None = None,
        source_json: dict | None = None,
        genome_json: dict | None = None,
        change_summary: str = "",
    ) -> PersonaGenomeRow:
        origin = str((source_json or {}).get("source_type") or "template")
        normalized = (
            normalize_persona_genome(genome_json)
            if genome_json is not None
            else build_default_persona_genome(
                name=companion_id,
                origin=origin,
                base_genome_id=base_genome_id,
            )
        )
        payload = persona_genome_to_json(normalized)
        payload["provenance"] = {
            **dict(payload.get("provenance") or {}),
            "companion_id": companion_id,
        }
        row = PersonaGenomeRow(
            genome_id=genome_id,
            companion_id=companion_id,
            version=version,
            status=status,
            base_genome_id=base_genome_id,
            schema_version=schema_version or normalized.schema_version,
            genome_hash=genome_hash or persona_genome_hash(payload),
            realizer_version=realizer_version or PERSONA_REALIZER,
            applied_event_id=applied_event_id,
            source_json=source_json or {},
            genome_json=payload,
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
