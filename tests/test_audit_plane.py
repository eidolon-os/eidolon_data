from __future__ import annotations

from datetime import UTC, datetime, timedelta

from eidolon_sdk.biz.audit import AuditEnvelope
from sqlalchemy import func, select

from eidolon_data import DataSettings, DataStore
from eidolon_data.audit import AuditOutboxDispatcher
from eidolon_data.audit.dispatcher import _retry_delay
from eidolon_data.schema.models import AuditOutboxRow


class _Publisher:
    def __init__(self, *, acknowledge: bool = True, fail: bool = False) -> None:
        self.acknowledge = acknowledge
        self.fail = fail
        self.events: list[AuditEnvelope] = []

    async def publish_many(self, events: list[AuditEnvelope]) -> set[str]:
        if self.fail:
            raise RuntimeError("transport unavailable")
        self.events.extend(events)
        return {event.event_id for event in events} if self.acknowledge else set()


async def _open_data_store(tmp_path) -> DataStore:
    store = DataStore.open(DataSettings(sqlite_path=str(tmp_path / "system.sqlite3")))
    await store.init_schema()
    return store


async def test_outbox_can_share_the_domain_transaction(tmp_path) -> None:
    store = await _open_data_store(tmp_path)
    try:
        async with store.session_factory() as session, session.begin():
            await store.audit_outbox.enqueue_in_session(
                session,
                event_id="audit-owner-created",
                producer="data",
                category="governance",
                owner_id="owner-1",
                subject_type="owner",
                subject_id="owner-1",
                action="owner.created",
                payload={"display_name": "Manson"},
            )

        pending = await store.audit_outbox.list_pending()
        assert len(pending) == 1
        assert pending[0].event_id == "audit-owner-created"
        assert pending[0].producer_seq == 1
        assert "actor_type" not in type(pending[0]).model_fields
        assert "actor_id" not in type(pending[0]).model_fields
    finally:
        await store.close()


async def test_system_mutations_emit_one_governance_fact_per_semantic_transaction(
    tmp_path,
) -> None:
    store = await _open_data_store(tmp_path)
    try:
        await store.owner_service.create_owner(
            owner_id="owner-audit",
            display_name="Audit Owner",
        )
        await store.workspace_provisioning.provision_workspace(
            owner_id="owner-audit",
            companion_id="companion-audit",
            genome_id="genome-audit",
            realm_id="realm-audit",
        )

        pending = await store.audit_outbox.list_pending()
        assert [event.action for event in pending] == [
            "owner.created",
            "companion.workspace.initialized",
        ]
        assert all(event.category == "governance" for event in pending)
    finally:
        await store.close()


async def test_dispatcher_marks_only_durably_acknowledged_events(tmp_path) -> None:
    store = await _open_data_store(tmp_path)
    try:
        await store.audit_outbox.enqueue(
            event_id="audit-1",
            producer="data",
            category="receipt",
            subject_type="persona_genome",
            subject_id="genome-1",
            action="persona.genome.committed",
        )
        publisher = _Publisher()
        dispatcher = AuditOutboxDispatcher(store.audit_outbox, publisher)

        assert await dispatcher.dispatch_once() == 1
        assert [event.event_id for event in publisher.events] == ["audit-1"]
        assert await store.audit_outbox.list_pending() == []
    finally:
        await store.close()


async def test_dispatcher_retains_events_when_transport_fails(tmp_path) -> None:
    store = await _open_data_store(tmp_path)
    try:
        await store.audit_outbox.enqueue(
            event_id="audit-retry",
            producer="data",
            category="governance",
            subject_type="owner",
            subject_id="owner-1",
            action="owner.deletion_requested",
        )
        dispatcher = AuditOutboxDispatcher(store.audit_outbox, _Publisher(fail=True))

        assert await dispatcher.dispatch_once() == 0
        # The row remains durable. Retry scheduling intentionally hides it from
        # the immediate pending batch instead of dropping it.
        assert await store.audit_outbox.list_pending() == []
        async with store.session_factory() as session:
            row = await session.scalar(
                select(AuditOutboxRow).where(
                    AuditOutboxRow.event_id == "audit-retry"
                )
            )
            assert row is not None
            assert row.attempt_count == 1
            assert row.published_at is None
            assert row.last_error == "RuntimeError: transport unavailable"
    finally:
        await store.close()


def test_retry_delay_is_exponential_and_bounded() -> None:
    base = timedelta(seconds=1)
    maximum = timedelta(seconds=60)

    assert _retry_delay(0, base=base, maximum=maximum) == timedelta(seconds=1)
    assert _retry_delay(1, base=base, maximum=maximum) == timedelta(seconds=2)
    assert _retry_delay(5, base=base, maximum=maximum) == timedelta(seconds=32)
    assert _retry_delay(6, base=base, maximum=maximum) == maximum
    assert _retry_delay(100, base=base, maximum=maximum) == maximum


async def test_purge_removes_only_published_outbox_rows(tmp_path) -> None:
    store = await _open_data_store(tmp_path)
    try:
        for event_id in ("audit-published", "audit-pending"):
            await store.audit_outbox.enqueue(
                event_id=event_id,
                producer="data",
                category="governance",
                subject_type="owner",
                subject_id="owner-1",
                action="owner.updated",
            )
        await store.audit_outbox.mark_published({"audit-published"})

        purged = await store.audit_outbox.purge_published(
            before=datetime.now(UTC) + timedelta(seconds=1)
        )

        assert purged == 1
        async with store.session_factory() as session:
            assert await session.scalar(select(func.count(AuditOutboxRow.outbox_id))) == 1
            remaining = await session.scalar(select(AuditOutboxRow.event_id))
            assert remaining == "audit-pending"
    finally:
        await store.close()
