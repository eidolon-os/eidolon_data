"""Data-local governance event facade backed only by audit_outbox."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from eidolon_data.events.facade import build_event
from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import AuditOutboxRow


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
    ) -> AuditOutboxRow:
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
        payload_json: dict | None = None,
    ) -> AuditOutboxRow:
        row = build_event(
            event_id=event_id,
            owner_id=owner_id,
            subject_type=subject_type,
            subject_id=subject_id,
            event_type=event_type,
            payload_json=payload_json or {},
            strict=False,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def list_for_subject(
        self, *, subject_type: str, subject_id: str
    ) -> list[AuditOutboxRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(AuditOutboxRow)
                .where(AuditOutboxRow.subject_type == subject_type)
                .where(AuditOutboxRow.subject_id == subject_id)
                .order_by(AuditOutboxRow.occurred_at, AuditOutboxRow.outbox_id)
            )
            return list(rows)

    async def list_by_trace(self, trace_id: str) -> list[AuditOutboxRow]:
        """All events sharing a correlation id, oldest first — the cross-service chain."""
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(AuditOutboxRow)
                .where(AuditOutboxRow.trace_id == trace_id)
                .order_by(AuditOutboxRow.occurred_at, AuditOutboxRow.outbox_id)
            )
            return list(rows)

    async def list_for_owner_since(
        self, owner_id: str, *, after: datetime | None = None, limit: int = 200
    ) -> list[AuditOutboxRow]:
        """Owner events strictly after ``after`` (by created_at), oldest first.

        Cursor tail for a near-real-time feed: the admin process polls this to
        stream new cross-process events over SSE. Callers dedupe by event_id, so
        a boundary re-send is harmless. Uses the (owner_id, created_at) index.
        """
        async with self._session_factory() as session:
            stmt = select(AuditOutboxRow).where(AuditOutboxRow.owner_id == owner_id)
            if after is not None:
                stmt = stmt.where(AuditOutboxRow.occurred_at > after)
            stmt = stmt.order_by(
                AuditOutboxRow.occurred_at, AuditOutboxRow.outbox_id
            ).limit(limit)
            rows = await session.scalars(stmt)
            return list(rows)

    async def list_for_owner(
        self, owner_id: str, *, limit: int = 100
    ) -> list[AuditOutboxRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(AuditOutboxRow)
                .where(AuditOutboxRow.owner_id == owner_id)
                .order_by(
                    AuditOutboxRow.occurred_at.desc(),
                    AuditOutboxRow.outbox_id.desc(),
                )
                .limit(limit)
            )
            return list(rows)
