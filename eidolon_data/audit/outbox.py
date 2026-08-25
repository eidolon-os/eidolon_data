"""Authority-local transactional audit outbox."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from eidolon_sdk.biz.audit import AuditEnvelope
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eidolon_data.schema import AuditOutboxRow


@dataclass(frozen=True)
class PendingAuditBatch:
    events: list[AuditEnvelope]
    max_attempt_count: int


@dataclass(frozen=True)
class OwnerGovernancePage:
    """One page of what happened to one Owner's things, newest first."""

    events: list[AuditEnvelope]
    #: The sequence a caller sends back to get the page before this one. ``None``
    #: means this is as far back as the Host still holds — which is not the same
    #: as "nothing happened before", and no layer above may say it is.
    next_sequence: int | None


@dataclass(frozen=True)
class AuditDeliveryState:
    event_id: str
    attempt_count: int
    published_at: datetime | None
    next_attempt_at: datetime
    last_error: str


class AuditOutboxRepository:
    """Persist audit intent locally; transport and global indexing are separate."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    @staticmethod
    def build_row(
        *,
        producer: str,
        category: str,
        subject_type: str,
        subject_id: str,
        action: str,
        event_id: str | None = None,
        owner_id: str | None = None,
        outcome: str = "success",
        severity: str = "info",
        reason: str | None = None,
        trace_id: str | None = None,
        data_classification: str = "safe",
        schema_version: int = 1,
        payload: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> AuditOutboxRow:
        """Build a row for ``session.add`` in the enclosing domain transaction."""

        return AuditOutboxRow(
            event_id=event_id or f"audit-{uuid4().hex}",
            producer=producer,
            category=category,
            owner_id=owner_id,
            subject_type=subject_type,
            subject_id=subject_id,
            action=action,
            outcome=outcome,
            severity=severity,
            reason=reason,
            trace_id=trace_id,
            data_classification=data_classification,
            schema_version=schema_version,
            payload_json=payload or {},
            occurred_at=occurred_at or datetime.now(UTC),
        )

    async def enqueue(self, **values: Any) -> AuditEnvelope:
        row = self.build_row(**values)
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
        return _envelope(row)

    async def enqueue_in_session(self, session: AsyncSession, **values: Any) -> AuditOutboxRow:
        row = self.build_row(**values)
        session.add(row)
        await session.flush()
        return row

    async def list_pending(self, *, limit: int = 200) -> list[AuditEnvelope]:
        return (await self.pending_batch(limit=limit)).events

    async def list_for_owner(
        self,
        owner_id: str,
        *,
        limit: int = 50,
        before_sequence: int | None = None,
    ) -> OwnerGovernancePage:
        """What has happened to this Owner's things, newest first.

        Reading the outbox rather than a second table, and that is a coupling
        worth saying out loud: **how far back this can see is decided by
        ``purge_published``**. Nothing dispatches today, so nothing is purged and
        this is the whole record — but whoever gives the dispatcher a purge
        horizon is also choosing how much history a person can still read, and
        the two must be decided together. The answer is called *recent* for that
        reason, and no caller may present it as everything that ever happened.

        Keyset on ``outbox_id`` because it is the insertion order this authority
        already guarantees; a timestamp cursor would tie two events written in
        the same millisecond.
        """

        capped = max(1, min(limit, 100))
        async with self._session_factory() as session:
            query = (
                select(AuditOutboxRow)
                .where(AuditOutboxRow.owner_id == owner_id)
                .where(AuditOutboxRow.category == "governance")
                .order_by(AuditOutboxRow.outbox_id.desc())
                .limit(capped + 1)
            )
            if before_sequence is not None:
                query = query.where(AuditOutboxRow.outbox_id < before_sequence)
            rows = list(await session.scalars(query))
        more = len(rows) > capped
        page = rows[:capped]
        return OwnerGovernancePage(
            events=[_envelope(row) for row in page],
            next_sequence=page[-1].outbox_id if more and page else None,
        )

    async def get_delivery_state(self, event_id: str) -> AuditDeliveryState | None:
        """Read transport state without exposing the persistence session."""

        async with self._session_factory() as session:
            row = await session.scalar(
                select(AuditOutboxRow).where(AuditOutboxRow.event_id == event_id)
            )
            if row is None:
                return None
            return AuditDeliveryState(
                event_id=row.event_id,
                attempt_count=row.attempt_count,
                published_at=_as_utc(row.published_at),
                next_attempt_at=_as_utc(row.next_attempt_at),
                last_error=row.last_error,
            )

    async def pending_batch(self, *, limit: int = 200) -> PendingAuditBatch:
        now = datetime.now(UTC)
        async with self._session_factory() as session:
            rows = list(
                await session.scalars(
                    select(AuditOutboxRow)
                    .where(AuditOutboxRow.published_at.is_(None))
                    .where(AuditOutboxRow.next_attempt_at <= now)
                    .order_by(AuditOutboxRow.outbox_id)
                    .limit(limit)
                )
            )
            return PendingAuditBatch(
                events=[_envelope(row) for row in rows],
                max_attempt_count=max((row.attempt_count for row in rows), default=0),
            )

    async def mark_published(
        self,
        event_ids: set[str],
        *,
        published_at: datetime | None = None,
    ) -> int:
        if not event_ids:
            return 0
        async with self._session_factory() as session:
            result = await session.execute(
                update(AuditOutboxRow)
                .where(AuditOutboxRow.event_id.in_(event_ids))
                .where(AuditOutboxRow.published_at.is_(None))
                .values(published_at=published_at or datetime.now(UTC), last_error="")
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def mark_failed(
        self,
        event_ids: set[str],
        *,
        error: str,
        retry_after: timedelta,
    ) -> int:
        if not event_ids:
            return 0
        async with self._session_factory() as session:
            result = await session.execute(
                update(AuditOutboxRow)
                .where(AuditOutboxRow.event_id.in_(event_ids))
                .where(AuditOutboxRow.published_at.is_(None))
                .values(
                    attempt_count=AuditOutboxRow.attempt_count + 1,
                    last_error=error[:2_000],
                    next_attempt_at=datetime.now(UTC) + retry_after,
                )
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def purge_published(self, *, before: datetime) -> int:
        async with self._session_factory() as session:
            result = await session.execute(
                delete(AuditOutboxRow)
                .where(AuditOutboxRow.published_at.is_not(None))
                .where(AuditOutboxRow.published_at < before)
            )
            await session.commit()
            return int(result.rowcount or 0)


def _envelope(row: AuditOutboxRow) -> AuditEnvelope:
    return AuditEnvelope(
        event_id=row.event_id,
        producer=row.producer,
        producer_seq=row.outbox_id,
        category=row.category,
        owner_id=row.owner_id,
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        action=row.action,
        outcome=row.outcome,
        severity=row.severity,
        reason=row.reason,
        trace_id=row.trace_id,
        data_classification=row.data_classification,
        schema_version=row.schema_version,
        payload=dict(row.payload_json or {}),
        occurred_at=_as_utc(row.occurred_at),
    )


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
