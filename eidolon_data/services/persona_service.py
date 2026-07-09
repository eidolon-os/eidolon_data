"""Persona service for companion genome workflows."""

from __future__ import annotations

from eidolon_data.repositories.companions import CompanionsRepository
from eidolon_data.repositories.events import EventsRepository
from eidolon_data.repositories.persona import PersonaGenomeConflict, PersonaRepository
from eidolon_data.schema.models import PersonaGenomeRow


class PersonaService:
    def __init__(
        self,
        *,
        persona_repo: PersonaRepository,
        companions: CompanionsRepository,
        events: EventsRepository,
    ) -> None:
        self._persona = persona_repo
        self._companions = companions
        self._events = events

    async def create_genome(
        self,
        *,
        genome_id: str,
        companion_id: str,
        owner_id: str,
        event_id: str,
        version: int = 1,
        genome_json: dict | None = None,
        source_json: dict | None = None,
        status: str = "committed",
        base_genome_id: str | None = None,
        change_summary: str = "",
    ) -> PersonaGenomeRow:
        genome = await self._persona.create_genome(
            genome_id=genome_id,
            companion_id=companion_id,
            version=version,
            status=status,
            base_genome_id=base_genome_id,
            source_json=source_json,
            genome_json=genome_json,
            applied_event_id=event_id if status == "committed" else None,
            change_summary=change_summary,
        )
        if status == "committed":
            await self._companions.set_current_genome(companion_id, genome_id)
        await self._events.record_event(
            event_id=event_id,
            owner_id=owner_id,
            companion_id=companion_id,
            subject_type="persona_genome",
            subject_id=genome_id,
            event_type="persona.genome.committed" if status == "committed" else "persona.evolution.proposed",
            payload_json={
                "genome_id": genome_id,
                "genome_hash": genome.genome_hash,
                "schema_version": genome.schema_version,
                "compiler_version": genome.compiler_version,
                "version": version,
                "status": status,
                "source": source_json or {},
            },
        )
        return genome

    async def get_current_genome(self, companion_id: str) -> PersonaGenomeRow | None:
        return await self._persona.get_current_genome(companion_id)

    async def create_genome_proposal(
        self,
        *,
        genome_id: str,
        companion_id: str,
        owner_id: str,
        base_genome_id: str,
        genome_json: dict | None = None,
        source_json: dict | None = None,
        change_summary: str = "",
    ) -> PersonaGenomeRow:
        genome = await self._persona.create_proposal(
            genome_id=genome_id,
            companion_id=companion_id,
            base_genome_id=base_genome_id,
            source_json=source_json,
            genome_json=genome_json,
            change_summary=change_summary,
        )
        await self._events.record_event(
            owner_id=owner_id,
            companion_id=companion_id,
            subject_type="persona_genome",
            subject_id=genome_id,
            event_type="persona.evolution.proposed",
            payload_json={
                "companion_id": companion_id,
                "base_genome_id": base_genome_id,
                "genome_id": genome_id,
                "genome_hash": genome.genome_hash,
                "version": genome.version,
                "change_summary": change_summary,
            },
        )
        return genome

    async def activate_genome(
        self,
        *,
        companion_id: str,
        owner_id: str,
        genome_id: str,
        expected_base_genome_id: str | None = None,
    ) -> PersonaGenomeRow:
        try:
            genome = await self._persona.activate_genome(
                companion_id=companion_id,
                genome_id=genome_id,
                expected_base_genome_id=expected_base_genome_id,
            )
        except PersonaGenomeConflict:
            await self._events.record_event(
                owner_id=owner_id,
                companion_id=companion_id,
                subject_type="persona_genome",
                subject_id=genome_id,
                event_type="persona.evolution.rejected",
                outcome="denied",
                payload_json={
                    "companion_id": companion_id,
                    "expected_base_genome_id": expected_base_genome_id,
                    "reason": "current genome changed before activation",
                },
            )
            raise
        await self._events.record_event(
            owner_id=owner_id,
            companion_id=companion_id,
            subject_type="persona_genome",
            subject_id=genome_id,
            event_type="persona.genome.committed",
            payload_json={
                "companion_id": companion_id,
                "genome_id": genome_id,
                "genome_hash": genome.genome_hash,
                "schema_version": genome.schema_version,
                "compiler_version": genome.compiler_version,
                "expected_base_genome_id": expected_base_genome_id,
            },
        )
        return genome

    async def reject_genome(
        self,
        *,
        owner_id: str,
        genome_id: str,
        reason: str = "",
    ) -> PersonaGenomeRow:
        genome = await self._persona.reject_genome(genome_id, reason=reason)
        await self._events.record_event(
            owner_id=owner_id,
            companion_id=genome.companion_id,
            subject_type="persona_genome",
            subject_id=genome_id,
            event_type="persona.evolution.rejected",
            payload_json={
                "companion_id": genome.companion_id,
                "genome_id": genome.genome_id,
                "genome_hash": genome.genome_hash,
                "schema_version": genome.schema_version,
                "compiler_version": genome.compiler_version,
                "reason": reason,
            },
        )
        return genome

    async def rollback_to_genome(
        self,
        *,
        owner_id: str,
        companion_id: str,
        genome_id: str,
    ) -> PersonaGenomeRow:
        genome = await self._persona.rollback_to_genome(
            companion_id=companion_id,
            genome_id=genome_id,
        )
        await self._events.record_event(
            owner_id=owner_id,
            companion_id=companion_id,
            subject_type="companion",
            subject_id=companion_id,
            event_type="persona.genome.rolled_back",
            payload_json={"genome_id": genome_id, "genome_hash": genome.genome_hash},
        )
        return genome

    async def reset_to_origin(
        self,
        *,
        owner_id: str,
        companion_id: str,
    ) -> PersonaGenomeRow:
        """Reset a companion to its authored origin genome (drops evolution drift)."""
        genome = await self._persona.rollback_to_origin_genome(companion_id=companion_id)
        await self._events.record_event(
            owner_id=owner_id,
            companion_id=companion_id,
            subject_type="companion",
            subject_id=companion_id,
            event_type="persona.genome.rolled_back",
            payload_json={
                "genome_id": genome.genome_id,
                "genome_hash": genome.genome_hash,
                "reason": "reset_to_origin",
            },
        )
        return genome
