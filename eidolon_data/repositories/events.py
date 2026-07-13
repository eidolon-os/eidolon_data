"""Event repository."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from eidolon_data.events.facade import build_event
from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import EventRow


class EventsRepository(Repository):
    async def record_event(
        self,
        *,
        event_type: str,
        owner_id: str,
        subject_type: str,
        subject_id: str,
        event_id: str | None = None,
        companion_id: str | None = None,
        actor_type: str = "system",
        actor_id: str | None = None,
        event_class: str | None = None,
        source: str | None = None,
        severity: str | None = None,
        outcome: str | None = None,
        reason: str | None = None,
        trace_id: str | None = None,
        data_classification: str = "safe",
        schema_version: int = 1,
        payload_json: dict | None = None,
        occurred_at: datetime | None = None,
        strict: bool = True,
    ) -> EventRow:
        """Contract-carrying facade: validate against the catalog, fill defaults, persist.

        This is the single entry point for standalone / fire-and-forget emits.
        For same-transaction governance events, use ``events.build_event`` and
        ``session.add(...)`` inside the caller's transaction instead.
        """
        row = build_event(
            event_type=event_type,
            owner_id=owner_id,
            subject_type=subject_type,
            subject_id=subject_id,
            event_id=event_id,
            companion_id=companion_id,
            actor_type=actor_type,
            actor_id=actor_id,
            event_class=event_class,
            source=source,
            severity=severity,
            outcome=outcome,
            reason=reason,
            trace_id=trace_id,
            data_classification=data_classification,
            schema_version=schema_version,
            payload_json=payload_json,
            occurred_at=occurred_at,
            strict=strict,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

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

    async def list_by_trace(self, trace_id: str) -> list[EventRow]:
        """All events sharing a correlation id, oldest first — the cross-service chain."""
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(EventRow)
                .where(EventRow.trace_id == trace_id)
                .order_by(EventRow.created_at)
            )
            return list(rows)

    async def list_for_owner_since(
        self, owner_id: str, *, after: datetime | None = None, limit: int = 200
    ) -> list[EventRow]:
        """Owner events strictly after ``after`` (by created_at), oldest first.

        Cursor tail for a near-real-time feed: the admin process polls this to
        stream new cross-process events over SSE. Callers dedupe by event_id, so
        a boundary re-send is harmless. Uses the (owner_id, created_at) index.
        """
        async with self._session_factory() as session:
            stmt = select(EventRow).where(EventRow.owner_id == owner_id)
            if after is not None:
                stmt = stmt.where(EventRow.created_at > after)
            stmt = stmt.order_by(EventRow.created_at).limit(limit)
            rows = await session.scalars(stmt)
            return list(rows)

    async def list_for_owner(self, owner_id: str, *, limit: int = 100) -> list[EventRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(EventRow)
                .where(EventRow.owner_id == owner_id)
                .order_by(EventRow.created_at.desc())
                .limit(limit)
            )
            return list(rows)
