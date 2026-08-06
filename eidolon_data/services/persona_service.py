"""Atomic command service for immutable persona genome workflows."""

from __future__ import annotations

from eidolon_sdk.biz.persona import (
    PERSONA_GENOME_SCHEMA,
    PERSONA_REALIZER,
    PersonaEvolutionProposalEvent,
    build_default_persona_genome,
    normalize_persona_genome,
    persona_genome_hash,
    persona_genome_to_json,
)
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eidolon_data.db.base import utc_now
from eidolon_data.events.facade import build_event
from eidolon_data.repositories.persona import PersonaGenomeConflict
from eidolon_data.schema.models import AuditOutboxRow, CompanionRow, PersonaGenomeRow


class PersonaService:
    """Owns every multi-row persona state transition.

    Genome insertion, current-pointer changes, and governance events share one
    database transaction.  Repositories remain useful for simple reads, but a
    persona commit must never be assembled from separately committed calls.
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

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
        async with self._session_factory() as session, session.begin():
            companion = await _owned_companion(session, owner_id, companion_id, lock=True)
            genome = _genome_row(
                genome_id=genome_id,
                companion_id=companion_id,
                owner_id=owner_id,
                version=version,
                status=status,
                base_genome_id=base_genome_id,
                source_json=source_json,
                genome_json=genome_json,
                applied_event_id=event_id if status == "committed" else None,
                change_summary=change_summary,
            )
            session.add(genome)
            await session.flush()
            if status == "committed":
                companion.current_genome_id = genome_id
                companion.updated_at = utc_now()
            _add_event(
                session,
                _event(
                    event_id=event_id,
                    owner_id=owner_id,
                    companion_id=companion_id,
                    genome=genome,
                    event_type=(
                        "persona.genome.committed"
                        if status == "committed"
                        else "persona.evolution.proposed"
                    ),
                    payload={
                        "companion_id": companion_id,
                        "genome_id": genome_id,
                        "genome_hash": genome.genome_hash,
                        "schema_version": genome.schema_version,
                        "realizer_version": genome.realizer_version,
                        "version": version,
                        "status": status,
                        "source": source_json or {},
                    },
                ),
            )
        return genome

    async def get_current_genome(self, companion_id: str) -> PersonaGenomeRow | None:
        async with self._session_factory() as session:
            companion = await session.get(CompanionRow, companion_id)
            if companion is None or not companion.current_genome_id:
                return None
            return await session.get(PersonaGenomeRow, companion.current_genome_id)

    async def create_evolution_proposal(
        self,
        proposal: PersonaEvolutionProposalEvent,
    ) -> PersonaGenomeRow:
        async with self._session_factory() as session, session.begin():
            companion = await _owned_companion(
                session,
                proposal.owner_id,
                proposal.companion_id,
                lock=True,
            )
            if companion.current_genome_id != proposal.base_genome_id:
                raise PersonaGenomeConflict(
                    "proposal base is not the current genome",
                    stale_genome_id=proposal.base_genome_id,
                )
            base = await session.get(PersonaGenomeRow, proposal.base_genome_id)
            if base is None or base.genome_hash != proposal.base_genome_hash:
                raise PersonaGenomeConflict(
                    "proposal base hash does not match current genome",
                    stale_genome_id=proposal.base_genome_id,
                )
            max_version = (
                await session.execute(
                    select(PersonaGenomeRow.version)
                    .where(PersonaGenomeRow.companion_id == proposal.companion_id)
                    .order_by(desc(PersonaGenomeRow.version))
                    .limit(1)
                )
            ).scalar_one_or_none()
            genome = _genome_row(
                genome_id=proposal.proposed_genome_id,
                companion_id=proposal.companion_id,
                owner_id=proposal.owner_id,
                version=(max_version or 0) + 1,
                status="proposed",
                base_genome_id=proposal.base_genome_id,
                source_json={
                    "source_type": "memory_reflection",
                    "proposal_id": proposal.proposal_id,
                    "base_genome_id": proposal.base_genome_id,
                    "base_genome_hash": proposal.base_genome_hash,
                    "evidence_refs": [
                        item.model_dump(mode="json") for item in proposal.evidence_refs
                    ],
                },
                genome_json=persona_genome_to_json(proposal.proposed_genome),
                change_summary=proposal.rationale,
            )
            session.add(genome)
            await session.flush()
            _add_event(
                session,
                _event(
                    owner_id=proposal.owner_id,
                    companion_id=proposal.companion_id,
                    genome=genome,
                    event_type="persona.evolution.proposed",
                    payload=proposal.model_dump(mode="json", exclude_none=True),
                ),
            )
        return genome

    async def approve_evolution(
        self,
        *,
        companion_id: str,
        owner_id: str,
        proposed_genome_id: str,
        expected_base_genome_id: str | None = None,
    ) -> PersonaGenomeRow:
        conflict = False
        async with self._session_factory() as session, session.begin():
            companion = await _owned_companion(session, owner_id, companion_id, lock=True)
            genome = await session.get(PersonaGenomeRow, proposed_genome_id)
            if genome is None or genome.companion_id != companion_id:
                raise KeyError(f"genome not found: {proposed_genome_id}")
            if genome.status != "proposed":
                raise ValueError("only proposed genomes can be approved")
            if expected_base_genome_id and companion.current_genome_id != expected_base_genome_id:
                genome.status = "stale"
                genome.updated_at = utc_now()
                conflict = True
                _add_event(
                    session,
                    _event(
                        owner_id=owner_id,
                        companion_id=companion_id,
                        genome=genome,
                        event_type="persona.evolution.rejected",
                        outcome="denied",
                        payload={
                            "companion_id": companion_id,
                            "genome_id": proposed_genome_id,
                            "expected_base_genome_id": expected_base_genome_id,
                            "reason": "current genome changed before activation",
                        },
                    ),
                )
            else:
                _add_event(
                    session,
                    _event(
                        owner_id=owner_id,
                        companion_id=companion_id,
                        genome=genome,
                        event_type="persona.evolution.approved",
                        payload={
                            "companion_id": companion_id,
                            "genome_id": proposed_genome_id,
                            "base_genome_id": genome.base_genome_id,
                            "proposal_id": (genome.source_json or {}).get("proposal_id"),
                        },
                    ),
                )
                genome.status = "committed"
                genome.updated_at = utc_now()
                companion.current_genome_id = proposed_genome_id
                companion.updated_at = utc_now()
                committed_event = _event(
                    owner_id=owner_id,
                    companion_id=companion_id,
                    genome=genome,
                    event_type="persona.genome.committed",
                    payload={
                        "companion_id": companion_id,
                        "genome_id": proposed_genome_id,
                        "genome_hash": genome.genome_hash,
                        "schema_version": genome.schema_version,
                        "realizer_version": genome.realizer_version,
                        "expected_base_genome_id": expected_base_genome_id,
                    },
                )
                genome.applied_event_id = committed_event.event_id
                _add_event(session, committed_event)
        if conflict:
            raise PersonaGenomeConflict(
                "current genome changed before activation",
                stale_genome_id=proposed_genome_id,
            )
        return genome

    async def reject_evolution(
        self,
        *,
        owner_id: str,
        genome_id: str,
        companion_id: str | None = None,
        reason: str = "",
    ) -> PersonaGenomeRow:
        async with self._session_factory() as session, session.begin():
            genome = await session.get(PersonaGenomeRow, genome_id)
            if genome is None:
                raise KeyError(f"genome not found: {genome_id}")
            if companion_id is not None and genome.companion_id != companion_id:
                raise KeyError(f"genome not found for companion: {genome_id}")
            await _owned_companion(session, owner_id, genome.companion_id, lock=True)
            if genome.status != "proposed":
                raise ValueError("only proposed genomes can be rejected")
            genome.status = "rejected"
            genome.change_summary = f"{genome.change_summary}\n\n{reason}".strip()
            genome.updated_at = utc_now()
            _add_event(
                session,
                _event(
                    owner_id=owner_id,
                    companion_id=genome.companion_id,
                    genome=genome,
                    event_type="persona.evolution.rejected",
                    outcome="denied",
                    payload={
                        "companion_id": genome.companion_id,
                        "genome_id": genome.genome_id,
                        "genome_hash": genome.genome_hash,
                        "reason": reason,
                    },
                ),
            )
        return genome

    async def rollback_to_genome(
        self,
        *,
        owner_id: str,
        companion_id: str,
        genome_id: str,
    ) -> PersonaGenomeRow:
        async with self._session_factory() as session, session.begin():
            companion = await _owned_companion(session, owner_id, companion_id, lock=True)
            genome = await session.get(PersonaGenomeRow, genome_id)
            if genome is None or genome.companion_id != companion_id:
                raise KeyError(f"genome not found: {genome_id}")
            if genome.status != "committed":
                raise ValueError("only committed genomes can be rollback targets")
            companion.current_genome_id = genome_id
            companion.updated_at = utc_now()
            _add_event(
                session,
                _event(
                    owner_id=owner_id,
                    companion_id=companion_id,
                    genome=genome,
                    event_type="persona.genome.rolled_back",
                    subject_type="companion",
                    subject_id=companion_id,
                    payload={"genome_id": genome_id, "genome_hash": genome.genome_hash},
                ),
            )
        return genome

    async def reset_to_origin(self, *, owner_id: str, companion_id: str) -> PersonaGenomeRow:
        async with self._session_factory() as session, session.begin():
            companion = await _owned_companion(session, owner_id, companion_id, lock=True)
            genome = (
                await session.get(PersonaGenomeRow, companion.current_genome_id)
                if companion.current_genome_id
                else None
            )
            if genome is None:
                raise KeyError(f"no genome for companion: {companion_id}")
            visited: set[str] = set()
            while genome.base_genome_id and genome.base_genome_id not in visited:
                visited.add(genome.genome_id)
                parent = await session.get(PersonaGenomeRow, genome.base_genome_id)
                if parent is None or parent.companion_id != companion_id:
                    break
                genome = parent
            companion.current_genome_id = genome.genome_id
            companion.updated_at = utc_now()
            _add_event(
                session,
                _event(
                    owner_id=owner_id,
                    companion_id=companion_id,
                    genome=genome,
                    event_type="persona.genome.rolled_back",
                    subject_type="companion",
                    subject_id=companion_id,
                    payload={
                        "genome_id": genome.genome_id,
                        "genome_hash": genome.genome_hash,
                        "reason": "reset_to_origin",
                    },
                ),
            )
        return genome


async def _owned_companion(session, owner_id: str, companion_id: str, *, lock: bool):
    stmt = select(CompanionRow).where(CompanionRow.companion_id == companion_id)
    if lock:
        stmt = stmt.with_for_update()
    companion = (await session.execute(stmt)).scalar_one_or_none()
    if companion is None or companion.owner_id != owner_id:
        raise KeyError(f"companion not found for owner: {companion_id}")
    return companion


def _genome_row(
    *,
    genome_id: str,
    companion_id: str,
    owner_id: str,
    version: int,
    status: str,
    base_genome_id: str | None,
    source_json: dict | None,
    genome_json: dict | None,
    applied_event_id: str | None = None,
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
        "owner_id": owner_id,
        "companion_id": companion_id,
    }
    return PersonaGenomeRow(
        genome_id=genome_id,
        companion_id=companion_id,
        version=version,
        status=status,
        base_genome_id=base_genome_id,
        schema_version=PERSONA_GENOME_SCHEMA,
        genome_hash=persona_genome_hash(payload),
        realizer_version=PERSONA_REALIZER,
        applied_event_id=applied_event_id,
        source_json=source_json or {},
        genome_json=payload,
        change_summary=change_summary,
    )


def _event(
    *,
    owner_id: str,
    companion_id: str,
    genome: PersonaGenomeRow,
    event_type: str,
    payload: dict,
    event_id: str | None = None,
    subject_type: str = "persona_genome",
    subject_id: str | None = None,
    outcome: str | None = None,
):
    return build_event(
        event_id=event_id,
        owner_id=owner_id,
        companion_id=companion_id,
        subject_type=subject_type,
        subject_id=subject_id or genome.genome_id,
        event_type=event_type,
        outcome=outcome,
        payload_json=payload,
    )


def _add_event(session: AsyncSession, event: AuditOutboxRow) -> None:
    """Write governance intent in the same System Data transaction."""
    session.add(event)
