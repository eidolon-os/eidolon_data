"""Maintenance operations for local development data."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from eidolon_data.schema.models import (
    CompanionRow,
    ConversationRow,
    DeviceRow,
    EventRow,
    JobRow,
    MemoryRealmRow,
    MessageRow,
    OwnerRow,
    PersonaGenomeRow,
    TurnRow,
)


@dataclass(frozen=True)
class OwnerCleanupResult:
    owner_id: str
    deleted: bool
    devices: int
    companions: int
    conversations: int
    jobs: int
    events: int


class MaintenanceService:
    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def delete_owner_tree(self, owner_id: str) -> OwnerCleanupResult:
        async with self._session_factory() as session:
            owner = await session.get(OwnerRow, owner_id)
            if owner is None:
                return OwnerCleanupResult(
                    owner_id=owner_id,
                    deleted=False,
                    devices=0,
                    companions=0,
                    conversations=0,
                    jobs=0,
                    events=0,
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
            turn_ids: list[str] = []
            if conversation_ids:
                turn_ids = list(
                    await session.scalars(
                        select(TurnRow.turn_id).where(TurnRow.conversation_id.in_(conversation_ids))
                    )
                )

            devices = await _count(session, select(DeviceRow.device_id).where(DeviceRow.owner_id == owner_id))
            conversations = len(conversation_ids)
            companions = len(companion_ids)
            jobs = await _count(session, select(JobRow.job_id).where(JobRow.owner_id == owner_id))
            events = await _count(session, select(EventRow.event_id).where(EventRow.owner_id == owner_id))

            if turn_ids:
                await session.execute(delete(MessageRow).where(MessageRow.turn_id.in_(turn_ids)))
            if conversation_ids:
                await session.execute(delete(TurnRow).where(TurnRow.conversation_id.in_(conversation_ids)))
                await session.execute(delete(ConversationRow).where(ConversationRow.owner_id == owner_id))
            if companion_ids:
                await session.execute(delete(PersonaGenomeRow).where(PersonaGenomeRow.companion_id.in_(companion_ids)))
            await session.execute(delete(MemoryRealmRow).where(MemoryRealmRow.owner_id == owner_id))
            await session.execute(delete(JobRow).where(JobRow.owner_id == owner_id))
            await session.execute(delete(EventRow).where(EventRow.owner_id == owner_id))
            await session.execute(delete(DeviceRow).where(DeviceRow.owner_id == owner_id))
            await session.execute(delete(CompanionRow).where(CompanionRow.owner_id == owner_id))
            await session.execute(delete(OwnerRow).where(OwnerRow.owner_id == owner_id))
            await session.commit()

            return OwnerCleanupResult(
                owner_id=owner_id,
                deleted=True,
                devices=devices,
                companions=companions,
                conversations=conversations,
                jobs=jobs,
                events=events,
            )


async def _count(session, statement) -> int:
    rows = await session.scalars(statement)
    return len(list(rows))
