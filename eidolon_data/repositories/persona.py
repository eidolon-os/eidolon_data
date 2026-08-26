"""Persistence for immutable Persona Genome snapshots, and going back to one.

Genomes are never edited. Returning to an earlier one appends a new version
carrying that older content, so the record of what this Companion has been
stays a record — and the act of going back is itself part of it, which is the
part someone will want to see later when they wonder what happened.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from eidolon_sdk.biz.persona import (
    PersonaAuthoring,
    PersonaAuthoringDraft,
    PersonaConflictCode,
    build_persona_genome_from_draft,
    persona_authoring_of,
    normalize_persona_genome,
    persona_genome_hash,
    persona_genome_to_json,
)
from sqlalchemy import func, select

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

    async def author(
        self,
        *,
        companion_id: str,
        persona: PersonaAuthoring,
        change_summary: str,
    ) -> PersonaGenomeRow:
        """Say who this Companion is now, as a new chapter.

        The same shape as :meth:`restore`, and that is the point: going back and
        changing your mind are the same act pointing in different directions.
        Both append; neither edits. So there is one way a Companion's persona
        ever changes, and the record of what it has been is only ever added to.

        **Saving without changing anything appends nothing.** What is compared
        is the authored part, not the genome hash: provenance lives inside that
        hash and names the genome each new one is based on, so two identical
        requests hash differently by construction. Opening the edit screen and
        pressing save must not manufacture a chapter, and neither must a phone
        retrying after a lost answer — a history padded with non-events is a
        history nobody reads, which costs exactly what the history is for.

        The name inside the genome is carried over rather than taken from the
        Companion row. Editing who an Eidolon *is* has no business rewriting
        what it is *called* as a side effect; if the two have drifted apart,
        that is renaming's problem and fixing it here would hide it.
        """

        async with self._session_factory() as session:
            companion = await session.get(CompanionRow, companion_id)
            if companion is None:
                raise PersonaGenomeConflict(
                    "companion does not exist", code="companion_missing"
                )
            current = (
                await session.get(PersonaGenomeRow, companion.current_genome_id)
                if companion.current_genome_id
                else None
            )
            if current is None:
                # Nothing to base a chapter on. A Companion with no genome is a
                # broken row, not a Companion waiting for its first persona:
                # provisioning writes one in the same transaction.
                raise PersonaGenomeConflict(
                    "companion has no current persona genome",
                    code="state_not_eligible",
                )

            standing = normalize_persona_genome(current.genome_json)
            if persona_authoring_of(standing) == persona:
                # Already who it is. Compared on the authored part rather than
                # on the genome hash, because provenance is inside that hash and
                # names the genome this one is based on — so two identical
                # requests hash differently by construction, and comparing
                # hashes would write a chapter every time regardless.
                return current

            name = standing.constitution.name or companion.display_name
            written = build_persona_genome_from_draft(
                PersonaAuthoringDraft.for_companion(persona, name=name),
                origin="owner_authored",
                base_genome_id=current.genome_id,
            )
            genome_json = persona_genome_to_json(written)
            genome_hash = persona_genome_hash(written)

            highest = await session.scalar(
                select(func.max(PersonaGenomeRow.version)).where(
                    PersonaGenomeRow.companion_id == companion_id
                )
            )
            now = datetime.now(timezone.utc)
            authored = PersonaGenomeRow(
                genome_id=f"g_{uuid4().hex}",
                companion_id=companion_id,
                version=(highest or 0) + 1,
                status="committed",
                base_genome_id=current.genome_id,
                schema_version=written.schema_version,
                genome_hash=genome_hash,
                realizer_version=current.realizer_version,
                source_json={
                    "source_type": "owner_authored",
                    "based_on": current.genome_id,
                    "based_on_version": current.version,
                },
                genome_json=genome_json,
                change_summary=change_summary,
                created_at=now,
                updated_at=now,
            )
            session.add(authored)
            # Flushed before the pointer moves, for the reason restore documents
            # below: the pointer is a foreign key into this table and a plain
            # string, so the unit of work has nothing to order on.
            await session.flush()
            companion.current_genome_id = authored.genome_id
            await session.commit()
            await session.refresh(authored)
            return authored

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
                raise PersonaGenomeConflict(
                    "persona genome does not belong to companion",
                    code="not_this_companion",
                )
            if target.status != "committed":
                # Only something this Companion actually was may be returned
                # to. A proposal it never became is not a past.
                raise PersonaGenomeConflict(
                    "only a committed persona genome can be restored",
                    code="state_not_eligible",
                    stale_genome_id=genome_id,
                )
            companion = await session.get(CompanionRow, companion_id)
            if companion is None:
                raise PersonaGenomeConflict(
                    "companion does not exist", code="companion_missing"
                )
            if companion.current_genome_id == genome_id:
                raise PersonaGenomeConflict(
                    "companion already has this persona genome",
                    # Not a concurrency conflict: the state asked for is the
                    # state it is in. Callers that treat this as success (the
                    # management restore does) rely on being able to tell it
                    # apart, which is exactly what a code is for.
                    code="state_not_eligible",
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
            # Flushed before the pointer moves. ``companions.current_genome_id``
            # is a foreign key into this table, and the unit of work has no
            # dependency to order on — the pointer is a plain string, not a
            # relationship — so without this the UPDATE can reach SQLite before
            # the INSERT and the constraint fails. Every restore did, and nothing
            # noticed: the only test of this route exercised the "already that"
            # refusal, so the success path had never run.
            await session.flush()
            companion.current_genome_id = restored.genome_id
            await session.commit()
            await session.refresh(restored)
            return restored
