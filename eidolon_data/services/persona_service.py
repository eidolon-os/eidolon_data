"""Atomic command service for immutable persona genome workflows."""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from uuid import uuid4

from eidolon_sdk.biz.persona import (
    PERSONA_GENOME_SCHEMA,
    PERSONA_REALIZER,
    ConversationPreferences,
    PersonaEditRequest,
    PersonaEditSnapshot,
    PersonaEvolutionProposalEvent,
    PersonaObservationEvent,
    apply_persona_authoring,
    build_default_persona_genome,
    normalize_persona_genome,
    persona_authoring_of,
    persona_genome_hash,
    persona_genome_to_json,
    validate_persona_evolution,
)
from sqlalchemy import desc, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from eidolon_data.audit import governance_fact
from eidolon_data.db.base import utc_now
from eidolon_data.repositories.persona import PersonaGenomeConflict
from eidolon_data.schema import CompanionRow, PersonaGenomeRow


class PersonaService:
    """Owns every multi-row persona state transition.

    Genome insertion, current-pointer changes, and governance events share one
    database transaction.  Repositories remain useful for simple reads, but a
    persona commit must never be assembled from separately committed calls.
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def read_edit_snapshot(self, companion_id: str) -> PersonaEditSnapshot:
        async with self._session_factory() as session:
            companion = await _companion_for_edit(session, companion_id)
            current = await _current_for_edit(session, companion)
            return _edit_snapshot(companion, current)

    async def edit(
        self,
        *,
        companion_id: str,
        request: PersonaEditRequest,
        change_summary: str = "更新性格与说话方式",
    ) -> PersonaEditSnapshot:
        """CAS, immutable version, preference update and receipt in one transaction."""
        fingerprint = hashlib.sha256(
            json.dumps(
                request.model_dump(mode="json", exclude_unset=True),
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        async with self._session_factory() as session, _persona_transaction(session):
            companion = await _companion_for_edit(session, companion_id)
            metadata = dict(companion.metadata_json or {})
            receipts = dict(metadata.get("persona_operations") or {})
            if receipt := receipts.get(request.operation_id):
                if receipt["fingerprint"] != fingerprint:
                    raise PersonaGenomeConflict(
                        "operation id reused with different input", code="operation_conflict"
                    )
                original = await session.get(PersonaGenomeRow, receipt["genome_id"])
                return PersonaEditSnapshot(
                    genome_id=original.genome_id,
                    display_name=receipt["display_name"],
                    companion_revision=receipt["companion_revision"],
                    persona=persona_authoring_of(normalize_persona_genome(original.genome_json)),
                    preferences=ConversationPreferences.model_validate(receipt["preferences"]),
                    preference_revision=receipt["preference_revision"],
                )
            current = await _current_for_edit(session, companion)
            before = _edit_snapshot(companion, current)
            if current.genome_id != request.expected_base_genome_id:
                raise PersonaGenomeConflict(
                    "persona changed; reload before saving",
                    code="base_not_current",
                    stale_genome_id=current.genome_id,
                )
            if before.preference_revision != request.expected_preference_revision:
                raise PersonaGenomeConflict(
                    "conversation preferences changed; reload before saving",
                    code="preferences_changed",
                    stale_genome_id=current.genome_id,
                )
            base = normalize_persona_genome(current.genome_json)
            target = None
            source = "owner_authored"
            if request.action == "rename":
                candidate = base.model_copy(deep=True)
                candidate.constitution.name = request.display_name.strip()
                companion.display_name = candidate.constitution.name
                source, change_summary = "owner_rename", "更新伙伴名字"
            elif request.action == "restore":
                target = await session.get(PersonaGenomeRow, request.restore_genome_id)
                if target is None or target.companion_id != companion_id:
                    raise PersonaGenomeConflict(
                        "persona does not belong to companion", code="not_this_companion"
                    )
                if target.status != "committed":
                    raise PersonaGenomeConflict(
                        "only committed personas can be restored", code="state_not_eligible"
                    )
                candidate = normalize_persona_genome(target.genome_json).model_copy(deep=True)
                candidate.constitution.name = companion.display_name
                if target.genome_id == current.genome_id:
                    candidate = base.model_copy(deep=True)
                source, change_summary = "owner_restore", "恢复性格设定"
            else:
                candidate = apply_persona_authoring(
                    base, request.persona, base_genome_id=current.genome_id
                )
            changed = candidate.model_dump(exclude={"provenance"}) != base.model_dump(
                exclude={"provenance"}
            )
            if changed or (target is not None and target.genome_id != current.genome_id):
                current = await _append_persona(
                    session,
                    companion,
                    current,
                    candidate,
                    source=source,
                    summary=change_summary,
                    restored_from=target,
                )
            if request.preferences is not None and request.preferences != before.preferences:
                config = dict(companion.runtime_config_json or {})
                config["conversation_preferences"] = request.preferences.model_dump(mode="json")
                config["preference_revision"] = before.preference_revision + 1
                companion.runtime_config_json = config
                companion.revision += 1
                companion.updated_at = utc_now()
            result = _edit_snapshot(companion, current)
            # Receipts live with the aggregate, not in the expiring audit outbox.
            # Store only the immutable result reference and the small preference
            # snapshot. Retain until Companion deletion for reliable late retries.
            receipts[request.operation_id] = {
                "fingerprint": fingerprint,
                "genome_id": result.genome_id,
                "display_name": result.display_name,
                "companion_revision": result.companion_revision,
                "preferences": result.preferences.model_dump(mode="json"),
                "preference_revision": result.preference_revision,
            }
            metadata["persona_operations"] = receipts
            companion.metadata_json = metadata
            session.add(
                governance_fact(
                    owner_id=companion.owner_id,
                    subject_type="companion",
                    subject_id=companion_id,
                    action="persona.settings.saved",
                    payload={
                        "genome_id": result.genome_id,
                        "previous_genome_id": before.genome_id,
                        "preference_revision": result.preference_revision,
                        "operation_id": request.operation_id,
                    },
                )
            )
            return result

    async def rename(self, companion_id: str, display_name: str) -> CompanionRow | None:
        name = display_name.strip()
        if not name or len(name) > 128:
            raise ValueError("companion name must contain 1-128 characters")
        async with self._session_factory() as session, _persona_transaction(session):
            companion = await session.get(CompanionRow, companion_id, with_for_update=True)
            if companion is None:
                return None
            current = await _current_for_edit(session, companion)
            base = normalize_persona_genome(current.genome_json)
            if companion.display_name == name and base.constitution.name == name:
                return companion
            candidate = base.model_copy(deep=True)
            candidate.constitution.name = name
            companion.display_name = name
            await _append_persona(
                session,
                companion,
                current,
                candidate,
                source="owner_rename",
                summary="更新伙伴名字",
            )
            return companion

    async def restore_chapter(
        self,
        *,
        companion_id: str,
        genome_id: str,
        change_summary: str = "恢复性格设定",
        owner_id: str | None = None,
    ) -> PersonaGenomeRow:
        async with self._session_factory() as session, _persona_transaction(session):
            companion = await _companion_for_edit(session, companion_id)
            if owner_id is not None and companion.owner_id != owner_id:
                raise KeyError("companion not found for owner")
            current = await _current_for_edit(session, companion)
            target = await session.get(PersonaGenomeRow, genome_id)
            if target is None or target.companion_id != companion_id:
                raise PersonaGenomeConflict(
                    "persona does not belong to companion", code="not_this_companion"
                )
            if target.status != "committed":
                raise PersonaGenomeConflict(
                    "only committed personas can be restored", code="state_not_eligible"
                )
            if current.genome_id == genome_id:
                return current
            if (current.source_json or {}).get("restored_from") == genome_id:
                return current
            candidate = normalize_persona_genome(target.genome_json).model_copy(deep=True)
            candidate.constitution.name = companion.display_name
            # Conversation preferences remain in the current Companion runtime config.
            return await _append_persona(
                session,
                companion,
                current,
                candidate,
                source="owner_restore",
                summary=change_summary,
                restored_from=target,
            )

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
        async with self._session_factory() as session, _persona_transaction(session):
            companion = await _owned_companion(session, owner_id, companion_id, lock=True)
            if base_genome_id is not None:
                base = await session.get(PersonaGenomeRow, base_genome_id)
                if base is None or base.companion_id != companion_id:
                    raise ValueError("base genome must belong to the same companion")
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

    async def record_observation(self, event: PersonaObservationEvent) -> None:
        """Record Agent evidence in the authority-local audit transaction."""
        async with self._session_factory() as session, _persona_transaction(session):
            await _owned_companion(
                session,
                event.owner_id,
                event.companion_id,
                lock=False,
            )
            session.add(
                governance_fact(
                    event_id=event.observation_id,
                    owner_id=event.owner_id,
                    subject_type="persona_observation",
                    subject_id=event.observation_id,
                    action="persona.observation.created",
                    payload=event.model_dump(mode="json", exclude_none=True),
                )
            )

    async def create_evolution_proposal(
        self,
        proposal: PersonaEvolutionProposalEvent,
    ) -> PersonaGenomeRow:
        async with self._session_factory() as session, _persona_transaction(session):
            companion = await _owned_companion(
                session,
                proposal.owner_id,
                proposal.companion_id,
                lock=True,
            )
            if companion.current_genome_id != proposal.base_genome_id:
                raise PersonaGenomeConflict(
                    "proposal base is not the current genome",
                    code="base_not_current",
                    stale_genome_id=proposal.base_genome_id,
                )
            base = await session.get(PersonaGenomeRow, proposal.base_genome_id)
            if base is None or base.genome_hash != proposal.base_genome_hash:
                raise PersonaGenomeConflict(
                    "proposal base hash does not match current genome",
                    code="base_hash_mismatch",
                    stale_genome_id=proposal.base_genome_id,
                )
            validate_persona_evolution(normalize_persona_genome(base.genome_json), proposal)
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
        async with self._session_factory() as session, _persona_transaction(session):
            companion = await _owned_companion(session, owner_id, companion_id, lock=True)
            genome = await session.get(PersonaGenomeRow, proposed_genome_id)
            if genome is None or genome.companion_id != companion_id:
                raise KeyError(f"genome not found: {proposed_genome_id}")
            if genome.status != "proposed":
                raise PersonaGenomeConflict(
                    "only proposed genomes can be approved", code="state_not_eligible"
                )
            if companion.current_genome_id != genome.base_genome_id or (
                expected_base_genome_id is not None
                and expected_base_genome_id != genome.base_genome_id
            ):
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
                base = await session.get(PersonaGenomeRow, genome.base_genome_id)
                candidate = normalize_persona_genome(genome.genome_json)
                validate_persona_evolution(
                    normalize_persona_genome(base.genome_json),
                    PersonaEvolutionProposalEvent(
                        proposal_id=(genome.source_json or {}).get("proposal_id", genome.genome_id),
                        owner_id=owner_id,
                        companion_id=companion_id,
                        base_genome_id=base.genome_id,
                        base_genome_hash=base.genome_hash,
                        proposed_genome_id=genome.genome_id,
                        proposed_genome=candidate,
                        rationale=genome.change_summary,
                        evidence_refs=(genome.source_json or {}).get("evidence_refs", []),
                    ),
                )
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
                companion.revision += 1
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
                code="current_changed",
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
        async with self._session_factory() as session, _persona_transaction(session):
            genome = await session.get(PersonaGenomeRow, genome_id)
            if genome is None:
                raise KeyError(f"genome not found: {genome_id}")
            if companion_id is not None and genome.companion_id != companion_id:
                raise KeyError(f"genome not found for companion: {genome_id}")
            await _owned_companion(session, owner_id, genome.companion_id, lock=True)
            if genome.status != "proposed":
                raise PersonaGenomeConflict(
                    "only proposed genomes can be rejected", code="state_not_eligible"
                )
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
        return await self.restore_chapter(
            owner_id=owner_id,
            companion_id=companion_id,
            genome_id=genome_id,
            change_summary="恢复历史人格",
        )

    async def reset_to_origin(self, *, owner_id: str, companion_id: str) -> PersonaGenomeRow:
        async with self._session_factory() as session, _persona_transaction(session):
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
            current = await _current_for_edit(session, companion)
            if (
                current.genome_id == genome.genome_id
                or (current.source_json or {}).get("restored_from") == genome.genome_id
            ):
                return current
            candidate = normalize_persona_genome(genome.genome_json).model_copy(deep=True)
            candidate.constitution.name = companion.display_name
            return await _append_persona(
                session,
                companion,
                current,
                candidate,
                source="owner_restore",
                summary="恢复初始性格设定",
                restored_from=genome,
            )


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
    return governance_fact(
        event_id=event_id,
        owner_id=owner_id,
        subject_type=subject_type,
        subject_id=subject_id or genome.genome_id,
        action=event_type,
        outcome=outcome or "success",
        payload={"companion_id": companion_id, **payload},
    )


def _add_event(session, event) -> None:
    """Write governance intent in the same System Data transaction."""
    session.add(event)


@asynccontextmanager
async def _persona_transaction(session):
    # SQLite ignores FOR UPDATE; reserve the writer before reading the base.
    # PostgreSQL uses row locks in the reads below.
    if session.bind.dialect.name == "sqlite":
        await session.execute(text("BEGIN IMMEDIATE"))
    else:
        await session.begin()
    try:
        yield
        await session.commit()
    except BaseException:
        await session.rollback()
        raise


async def _companion_for_edit(session, companion_id):
    companion = await session.get(CompanionRow, companion_id, with_for_update=True)
    if companion is None:
        raise PersonaGenomeConflict("companion missing", code="companion_missing")
    return companion


async def _current_for_edit(session, companion):
    current = await session.get(PersonaGenomeRow, companion.current_genome_id or "")
    if current is None or current.status != "committed":
        raise PersonaGenomeConflict("no committed persona", code="state_not_eligible")
    return current


def _edit_snapshot(companion, current):
    config = companion.runtime_config_json or {}
    return PersonaEditSnapshot(
        genome_id=current.genome_id,
        display_name=companion.display_name,
        companion_revision=companion.revision,
        persona=persona_authoring_of(normalize_persona_genome(current.genome_json)),
        preferences=ConversationPreferences.model_validate(
            config.get("conversation_preferences", {})
        ),
        preference_revision=config.get("preference_revision", 1),
    )


async def _append_persona(
    session, companion, current, candidate, *, source, summary, restored_from=None
):
    candidate = candidate.model_copy(deep=True)
    candidate.provenance.origin = source
    candidate.provenance.base_genome_id = current.genome_id
    highest = await session.scalar(
        select(func.max(PersonaGenomeRow.version)).where(
            PersonaGenomeRow.companion_id == companion.companion_id
        )
    )
    row = _genome_row(
        genome_id=f"g_{uuid4().hex}",
        companion_id=companion.companion_id,
        owner_id=companion.owner_id,
        version=(highest or 0) + 1,
        status="committed",
        base_genome_id=current.genome_id,
        source_json={
            "source_type": source,
            "previous_genome_id": current.genome_id,
            **(
                {
                    "restored_from": restored_from.genome_id,
                    "restored_version": restored_from.version,
                }
                if restored_from
                else {}
            ),
        },
        genome_json=persona_genome_to_json(candidate),
        change_summary=summary,
    )
    session.add(row)
    await session.flush()
    companion.current_genome_id = row.genome_id
    companion.revision += 1
    companion.updated_at = utc_now()
    event = _event(
        owner_id=companion.owner_id,
        companion_id=companion.companion_id,
        genome=row,
        event_type="persona.genome.committed",
        payload={
            "genome_id": row.genome_id,
            "previous_genome_id": current.genome_id,
            "source": source,
        },
    )
    row.applied_event_id = event.event_id
    session.add(event)
    if restored_from is not None:
        session.add(
            _event(
                owner_id=companion.owner_id,
                companion_id=companion.companion_id,
                genome=row,
                event_type="persona.genome.rolled_back",
                payload={"genome_id": row.genome_id, "restored_from": restored_from.genome_id},
            )
        )
    return row
