"""Event repository."""

from __future__ import annotations

from sqlalchemy import select

from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import EventRow


class EventsRepository(Repository):
    async def append(
        self,
        *,
        event_id: str,
        owner_id: str,
        subject_type: str,
        subject_id: str,
        event_type: str,
        actor_type: str = "system",
        actor_id: str | None = None,
        payload_json: dict | None = None,
    ) -> EventRow:
        row = EventRow(
            event_id=event_id,
            owner_id=owner_id,
            subject_type=subject_type,
            subject_id=subject_id,
            event_type=event_type,
            actor_type=actor_type,
            actor_id=actor_id,
            payload_json=payload_json or {},
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def list_for_subject(self, *, subject_type: str, subject_id: str) -> list[EventRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(EventRow)
                .where(EventRow.subject_type == subject_type)
                .where(EventRow.subject_id == subject_id)
                .order_by(EventRow.created_at)
            )
            return list(rows)

