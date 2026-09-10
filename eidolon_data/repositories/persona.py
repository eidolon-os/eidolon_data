"""Persistence for immutable Persona Genome snapshots, and going back to one.

Genomes are never edited. Returning to an earlier one appends a new version
carrying that older content, so the record of what this Companion has been
stays a record — and the act of going back is itself part of it, which is the
part someone will want to see later when they wonder what happened.
"""

from __future__ import annotations

from eidolon_sdk.biz.persona import (
    PersonaConflictCode,
)
from sqlalchemy import select

from eidolon_data.repositories.base import Repository
from eidolon_data.schema import CompanionRow, PersonaGenomeRow


class PersonaGenomeConflict(RuntimeError):
    """A persona mutation the authority refused, and why in a word.

    ``code`` is the part a caller can act on. The message is for a person reading
    a log; matching on it across a process boundary is what a consumer had to do
    before this, and it made "someone changed it while you were deciding" — worth
    a re-read and another try — indistinguishable from "that genome is not this
    Companion's", which is never worth retrying.

    The vocabulary lives in ``eidolon_sdk.biz.persona`` because the producer is
    not its only reader.
    """

    def __init__(
        self,
        message: str,
        *,
        code: PersonaConflictCode,
        stale_genome_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
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
