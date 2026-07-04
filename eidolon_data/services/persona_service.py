"""Persona service for companion genome workflows."""

from __future__ import annotations

from uuid import uuid4

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
        prompt_markdown: str = "",
        source_json: dict | None = None,
        evolution_state_json: dict | None = None,
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
            prompt_markdown=prompt_markdown,
            evolution_state_json=evolution_state_json,
            change_summary=change_summary,
        )
        if status == "committed":
            await self._companions.set_current_genome(companion_id, genome_id)
        await self._events.append(
            event_id=event_id,
            owner_id=owner_id,
            subject_type="companion",
            subject_id=companion_id,
            event_type="persona.genome.created",
            payload_json={
                "genome_id": genome_id,
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
        prompt_markdown: str = "",
        source_json: dict | None = None,
        evolution_state_json: dict | None = None,
        change_summary: str = "",
    ) -> PersonaGenomeRow:
        genome = await self._persona.create_proposal(
            genome_id=genome_id,
            companion_id=companion_id,
            base_genome_id=base_genome_id,
            source_json=source_json,
            genome_json=genome_json,
            prompt_markdown=prompt_markdown,
            evolution_state_json=evolution_state_json,
            change_summary=change_summary,
        )
        await self._events.append(
            event_id=_event_id(),
            owner_id=owner_id,
            subject_type="persona_genome",
            subject_id=genome_id,
            event_type="persona_genome.proposed",
            payload_json={
                "companion_id": companion_id,
                "base_genome_id": base_genome_id,
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
            await self._events.append(
                event_id=_event_id(),
                owner_id=owner_id,
                subject_type="persona_genome",
                subject_id=genome_id,
                event_type="persona_genome.stale",
                payload_json={
                    "companion_id": companion_id,
                    "expected_base_genome_id": expected_base_genome_id,
                },
            )
            raise
        await self._events.append(
            event_id=_event_id(),
            owner_id=owner_id,
            subject_type="persona_genome",
            subject_id=genome_id,
            event_type="persona_genome.activated",
            payload_json={
                "companion_id": companion_id,
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
        await self._events.append(
            event_id=_event_id(),
            owner_id=owner_id,
            subject_type="persona_genome",
            subject_id=genome_id,
            event_type="persona_genome.rejected",
            payload_json={"reason": reason},
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
        await self._events.append(
            event_id=_event_id(),
            owner_id=owner_id,
            subject_type="companion",
            subject_id=companion_id,
            event_type="persona_genome.rollback",
            payload_json={"genome_id": genome_id},
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
        await self._events.append(
            event_id=_event_id(),
            owner_id=owner_id,
            subject_type="companion",
            subject_id=companion_id,
            event_type="persona_genome.reset_to_origin",
            payload_json={"genome_id": genome.genome_id},
        )
        return genome


def _event_id() -> str:
    return f"evt_{uuid4().hex}"
