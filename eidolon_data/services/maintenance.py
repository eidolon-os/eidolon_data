"""Maintenance operations for local development data."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, or_, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from eidolon_data.schema.models import (
    BodyCommandRow,
    CompanionRow,
    ConversationRow,
    DeviceRow,
    EventRow,
    JobRow,
    MemoryRealmRow,
    MessageRow,
    OwnerRow,
    PersonaGenomeRow,
    RuntimeCallerRow,
    RuntimeSessionRow,
    TurnRow,
)


@dataclass(frozen=True)
class OwnerCleanupResult:
    owner_id: str
    deleted: bool
    devices: int
    companions: int
    persona_genomes: int
    memory_realms: int
    body_commands: int
    runtime_callers: int
    runtime_sessions: int
    messages: int
    turns: int
    conversations: int
    jobs: int
    events: int
    realm_ids: list[str]


class MaintenanceService:
    """Local-development destructive maintenance operations."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def delete_owner_tree(self, owner_id: str) -> OwnerCleanupResult:
        """Hard-delete one owner and all owned rows.

        This is intentionally broader than privacy/data-governance deletion:
        it removes the owner identity row itself and is meant for local dev
        cleanup, fixtures, and operator-confirmed maintenance.
        """

        async with self._session_factory() as session:
            owner = await session.get(OwnerRow, owner_id)
            if owner is None:
                return OwnerCleanupResult(
                    owner_id=owner_id,
                    deleted=False,
                    devices=0,
                    companions=0,
                    persona_genomes=0,
                    memory_realms=0,
                    body_commands=0,
                    runtime_callers=0,
                    runtime_sessions=0,
                    messages=0,
                    turns=0,
                    conversations=0,
                    jobs=0,
                    events=0,
                    realm_ids=[],
                )

            companion_ids = list(
                await session.scalars(
                    select(CompanionRow.companion_id).where(CompanionRow.owner_id == owner_id)
                )
            )
            conversation_ids = list(
                await session.scalars(
                    select(ConversationRow.conversation_id).where(ConversationRow.owner_id == owner_id)
                )
            )
            turn_ids = list(
                await session.scalars(
                    select(TurnRow.turn_id).where(TurnRow.conversation_id.in_(conversation_ids))
                )
            ) if conversation_ids else []
            realm_ids = list(
                await session.scalars(
                    select(MemoryRealmRow.realm_id).where(MemoryRealmRow.owner_id == owner_id)
                )
            )
            device_ids = list(
                await session.scalars(
                    select(DeviceRow.device_id).where(DeviceRow.owner_id == owner_id)
                )
            )
            body_command_condition = _body_command_owner_condition(
                owner_id=owner_id,
                companion_ids=companion_ids,
                device_ids=device_ids,
            )

            messages = await _count(
                session,
                select(MessageRow.message_id).where(MessageRow.turn_id.in_(turn_ids)),
            ) if turn_ids else 0
            persona_genomes = await _count(
                session,
                select(PersonaGenomeRow.genome_id).where(
                    PersonaGenomeRow.companion_id.in_(companion_ids)
                ),
            ) if companion_ids else 0

            result = OwnerCleanupResult(
                owner_id=owner_id,
                deleted=True,
                devices=len(device_ids),
                companions=len(companion_ids),
                persona_genomes=persona_genomes,
                memory_realms=len(realm_ids),
                body_commands=await _count(
                    session,
                    select(BodyCommandRow.command_id).where(body_command_condition),
                ),
                runtime_callers=await _count(
                    session,
                    select(RuntimeCallerRow.caller_id).where(RuntimeCallerRow.owner_id == owner_id),
                ),
                runtime_sessions=await _count(
                    session,
                    select(RuntimeSessionRow.session_id).where(RuntimeSessionRow.owner_id == owner_id),
                ),
                messages=messages,
                turns=len(turn_ids),
                conversations=len(conversation_ids),
                jobs=await _count(session, select(JobRow.job_id).where(JobRow.owner_id == owner_id)),
                events=await _count(session, select(EventRow.event_id).where(EventRow.owner_id == owner_id)),
                realm_ids=realm_ids,
            )

            if turn_ids:
                await session.execute(delete(MessageRow).where(MessageRow.turn_id.in_(turn_ids)))
            if conversation_ids:
                await session.execute(delete(TurnRow).where(TurnRow.conversation_id.in_(conversation_ids)))
                await session.execute(delete(ConversationRow).where(ConversationRow.owner_id == owner_id))
            await session.execute(delete(BodyCommandRow).where(body_command_condition))
            await session.execute(delete(RuntimeSessionRow).where(RuntimeSessionRow.owner_id == owner_id))
            await session.execute(delete(RuntimeCallerRow).where(RuntimeCallerRow.owner_id == owner_id))
            if companion_ids:
                await session.execute(delete(PersonaGenomeRow).where(PersonaGenomeRow.companion_id.in_(companion_ids)))
            await session.execute(delete(MemoryRealmRow).where(MemoryRealmRow.owner_id == owner_id))
            await session.execute(delete(JobRow).where(JobRow.owner_id == owner_id))
            await session.execute(delete(EventRow).where(EventRow.owner_id == owner_id))
            await session.execute(delete(DeviceRow).where(DeviceRow.owner_id == owner_id))
            await session.execute(delete(CompanionRow).where(CompanionRow.owner_id == owner_id))
            await session.execute(delete(OwnerRow).where(OwnerRow.owner_id == owner_id))
            await session.commit()
            return result


async def _count(session, statement) -> int:
    return len(list(await session.scalars(statement)))


def _body_command_owner_condition(
    *,
    owner_id: str,
    companion_ids: list[str],
    device_ids: list[str],
):
    clauses = [BodyCommandRow.owner_id == owner_id]
    if companion_ids:
        clauses.append(BodyCommandRow.companion_id.in_(companion_ids))
    if device_ids:
        clauses.extend(
            (
                BodyCommandRow.device_id.in_(device_ids),
                BodyCommandRow.source_device_id.in_(device_ids),
            )
        )
    return or_(*clauses)
