"""Compatibility access facades; repositories remain read-only.

DataStore composes reads with the single persona command authority here.
"""

from eidolon_sdk.biz.persona import PersonaAuthoring

from eidolon_data.repositories.companions import CompanionsRepository
from eidolon_data.repositories.persona import PersonaRepository
from eidolon_data.schema import CompanionRow, PersonaGenomeRow
from eidolon_data.services.persona_service import PersonaService


class PersonaAccess(PersonaRepository):
    def __init__(self, session_factory):
        super().__init__(session_factory)
        self._commands = PersonaService(session_factory)

    async def author(
        self,
        *,
        companion_id: str,
        persona: PersonaAuthoring,
        change_summary: str,
        expected_base_genome_id: str,
        operation_id: str,
        expected_preference_revision: int = 1,
    ) -> PersonaGenomeRow:
        from eidolon_sdk.biz.persona import PersonaEditRequest

        result = await self._commands.edit(
            companion_id=companion_id,
            change_summary=change_summary,
            request=PersonaEditRequest(
                persona=persona,
                operation_id=operation_id,
                expected_base_genome_id=expected_base_genome_id,
                expected_preference_revision=expected_preference_revision,
            ),
        )
        return await self.get(result.genome_id)

    async def restore(
        self, *, companion_id: str, genome_id: str, change_summary: str
    ) -> PersonaGenomeRow:
        return await self._commands.restore_chapter(
            companion_id=companion_id, genome_id=genome_id, change_summary=change_summary
        )


class CompanionsAccess(CompanionsRepository):
    def __init__(self, session_factory):
        super().__init__(session_factory)
        self._commands = PersonaService(session_factory)

    async def rename(self, companion_id: str, display_name: str) -> CompanionRow | None:
        """Give this Companion the name its Owner chose.

        Returns None when there is no such Companion, so the caller answers
        "which Companion?" rather than reporting a rename that touched nothing.
        """

        return await self._commands.rename(companion_id, display_name)
