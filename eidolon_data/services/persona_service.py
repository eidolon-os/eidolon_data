"""Persona service for companion genome workflows."""

from __future__ import annotations

from eidolon_data.repositories.companions import CompanionsRepository
from eidolon_data.repositories.events import EventsRepository
from eidolon_data.repositories.persona import PersonaRepository
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
        evolution_state_json: dict | None = None,
    ) -> PersonaGenomeRow:
        genome = await self._persona.create_genome(
            genome_id=genome_id,
            companion_id=companion_id,
            version=version,
            source_json=source_json,
            genome_json=genome_json,
            evolution_state_json=evolution_state_json,
        )
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
                "source": source_json or {},
            },
        )
        return genome

    async def get_current_genome(self, companion_id: str) -> PersonaGenomeRow | None:
        return await self._persona.get_current_genome(companion_id)
