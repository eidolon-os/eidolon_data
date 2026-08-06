from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from eidolon_sdk.biz.audit import AuditEnvelope

from eidolon_data.audit import AuditOutboxDispatcher

pytestmark = pytest.mark.component


class Publisher:
    def __init__(self, *, fail: bool = False, acknowledge: set[str] | None = None) -> None:
        self.fail = fail
        self.acknowledge = acknowledge
        self.events: list[AuditEnvelope] = []

    async def publish_many(self, events: list[AuditEnvelope]) -> set[str]:
        if self.fail:
            raise RuntimeError("transport unavailable")
        self.events.extend(events)
        if self.acknowledge is not None:
            return set(self.acknowledge)
        return {event.event_id for event in events}


async def _enqueue(store, event_id: str) -> None:
    await store.audit_outbox.enqueue(
        event_id=event_id,
        producer="test-authority",
        category="receipt",
        subject_type="test",
        subject_id=event_id,
        action="test.received",
    )


async def test_dispatch_marks_only_acknowledged_events(store) -> None:
    await _enqueue(store, "audit-1")
    publisher = Publisher()
    dispatcher = AuditOutboxDispatcher(store.audit_outbox, publisher)
    assert await dispatcher.dispatch_once() == 1
    assert [event.event_id for event in publisher.events] == ["audit-1"]
    state = await store.audit_outbox.get_delivery_state("audit-1")
    assert state is not None and state.published_at is not None
    assert await store.audit_outbox.list_pending() == []


async def test_transport_failure_is_durable_and_backed_off(store) -> None:
    await _enqueue(store, "audit-retry")
    dispatcher = AuditOutboxDispatcher(store.audit_outbox, Publisher(fail=True))
    before = datetime.now(UTC)
    assert await dispatcher.dispatch_once() == 0
    state = await store.audit_outbox.get_delivery_state("audit-retry")
    assert state is not None
    assert state.attempt_count == 1
    assert state.published_at is None
    assert state.next_attempt_at > before
    assert state.last_error == "RuntimeError: transport unavailable"
    assert await store.audit_outbox.list_pending() == []


async def test_partial_acknowledgement_publishes_one_and_retries_one(store) -> None:
    await _enqueue(store, "audit-ok")
    await _enqueue(store, "audit-missing")
    dispatcher = AuditOutboxDispatcher(
        store.audit_outbox,
        Publisher(acknowledge={"audit-ok", "not-in-batch"}),
    )
    assert await dispatcher.dispatch_once() == 1
    ok = await store.audit_outbox.get_delivery_state("audit-ok")
    missing = await store.audit_outbox.get_delivery_state("audit-missing")
    assert ok is not None and ok.published_at is not None
    assert missing is not None and missing.attempt_count == 1
    assert missing.last_error == "transport did not acknowledge event"


async def test_purge_removes_only_old_published_rows(store) -> None:
    await _enqueue(store, "audit-published")
    await _enqueue(store, "audit-pending")
    assert await store.audit_outbox.mark_published({"audit-published"}) == 1
    assert (
        await store.audit_outbox.purge_published(before=datetime.now(UTC) + timedelta(seconds=1))
        == 1
    )
    assert await store.audit_outbox.get_delivery_state("audit-published") is None
    assert await store.audit_outbox.get_delivery_state("audit-pending") is not None


async def test_empty_outbox_and_empty_updates_are_noops(store) -> None:
    assert await AuditOutboxDispatcher(store.audit_outbox, Publisher()).dispatch_once() == 0
    assert await store.audit_outbox.mark_published(set()) == 0
    assert (
        await store.audit_outbox.mark_failed(set(), error="none", retry_after=timedelta(seconds=1))
        == 0
    )
