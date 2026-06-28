"""Owner-scoped hard-delete workflows."""

from __future__ import annotations

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from eidolon_data.schema.models import (
    CompanionRow,
    ConversationRow,
    DeviceRow,
    EventRow,
    JobRow,
    MemoryRealmRow,
    MessageRow,
    PersonaGenomeRow,
    TurnRow,
)


class OwnerDataService:
    """Data-governance operations scoped to one owner."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def delete_owner_data(self, owner_id: str) -> dict[str, int]:
        """Delete business data owned by ``owner_id`` while keeping owner identity.

        The owner row itself is deliberately retained so account ownership can
        be managed by its own lifecycle.
        """

        async with self._session_factory() as session, session.begin():
            conversation_ids = list(
                (
                    await session.execute(
                        select(ConversationRow.conversation_id).where(
                            ConversationRow.owner_id == owner_id
                        )
                    )
                ).scalars()
            )
            turn_ids: list[str] = []
            if conversation_ids:
                turn_ids = list(
                    (
                        await session.execute(
                            select(TurnRow.turn_id).where(
                                TurnRow.conversation_id.in_(conversation_ids)
                            )
                        )
                    ).scalars()
                )

            companion_ids = list(
                (
                    await session.execute(
                        select(CompanionRow.companion_id).where(
                            CompanionRow.owner_id == owner_id
                        )
                    )
                ).scalars()
            )
            counts: dict[str, int] = {}
            counts["messages"] = (
                _rowcount(
                    await session.execute(
                        delete(MessageRow).where(MessageRow.turn_id.in_(turn_ids))
                    )
                )
                if turn_ids
                else 0
            )
            counts["turns"] = (
                _rowcount(
                    await session.execute(
                        delete(TurnRow).where(TurnRow.conversation_id.in_(conversation_ids))
                    )
                )
                if conversation_ids
                else 0
            )
            counts["conversations"] = _rowcount(
                await session.execute(
                    delete(ConversationRow).where(ConversationRow.owner_id == owner_id)
                )
            )
            counts["memory_realms"] = _rowcount(
                await session.execute(
                    delete(MemoryRealmRow).where(MemoryRealmRow.owner_id == owner_id)
                )
            )
            counts["jobs"] = _rowcount(
                await session.execute(delete(JobRow).where(JobRow.owner_id == owner_id))
            )
            counts["devices"] = _rowcount(
                await session.execute(delete(DeviceRow).where(DeviceRow.owner_id == owner_id))
            )
            counts["events"] = _rowcount(
                await session.execute(delete(EventRow).where(EventRow.owner_id == owner_id))
            )
            counts["persona_genomes"] = (
                _rowcount(
                    await session.execute(
                        delete(PersonaGenomeRow).where(
                            PersonaGenomeRow.companion_id.in_(companion_ids)
                        )
                    )
                )
                if companion_ids
                else 0
            )
            counts["companions_marked_deleted"] = (
                _rowcount(
                    await session.execute(
                        update(CompanionRow)
                        .where(CompanionRow.owner_id == owner_id)
                        .values(
                            current_genome_id=None,
                            default_memory_realm_id=None,
                            status="deleted",
                        )
                    )
                )
                if companion_ids
                else 0
            )
            return counts


def _rowcount(result) -> int:
    return int(result.rowcount or 0)


__all__ = ["OwnerDataService"]
