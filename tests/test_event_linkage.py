"""L4 — cross-service fanout handshake linkage (see docs §14).

Simulates both ends via the facade (no real agent/memory processes): the agent
publishes ``eidolon.memory.fanout.status`` and memory confirms
``memory.fanout.absorbed`` for the same turn + trace. Asserts both the
subject-based (turn) and trace-based views return the full chain — proving the
"published → absorbed" delivery story is reconstructable.
"""

from __future__ import annotations

import asyncio

from eidolon_data import DataStore
from eidolon_data.schema.models import OwnerRow


async def _owner(store: DataStore, owner_id: str) -> None:
    async with store.session_factory() as session:
        session.add(OwnerRow(owner_id=owner_id, display_name="O"))
        await session.commit()


async def test_fanout_handshake_links_by_subject_and_trace(store: DataStore) -> None:
    await _owner(store, "owner-lnk")
    trace = "trace-xyz"
    turn_id = "turn-lnk"

    # agent side: published to NATS (not yet absorbed).
    await store.events.record_event(
        event_type="eidolon.memory.fanout.status",
        owner_id="owner-lnk",
        companion_id="c1",
        subject_type="turn",
        subject_id=turn_id,
        actor_type="agent",
        trace_id=trace,
        payload_json={"turn_id": turn_id, "state": "published"},
    )
    # memory side: absorbed the same turn under the same trace.
    await store.events.record_event(
        event_type="memory.fanout.absorbed",
        owner_id="owner-lnk",
        companion_id="c1",
        subject_type="turn",
        subject_id=turn_id,
        actor_type="memory",
        trace_id=trace,
        payload_json={"should_write": True, "fragments": 2},
    )

    # Subject-based linkage: both halves of the handshake hang off the turn.
    by_subject = await store.events.list_for_subject(subject_type="turn", subject_id=turn_id)
    assert {e.event_type for e in by_subject} == {
        "eidolon.memory.fanout.status",
        "memory.fanout.absorbed",
    }
    assert {e.source for e in by_subject} == {"agent", "memory"}

    # Trace-based linkage: cross-service correlation id ties the chain.
    by_trace = await store.events.list_by_trace(trace)
    assert {e.source for e in by_trace} == {"agent", "memory"}
    assert all(e.trace_id == trace for e in by_trace)
    assert len(by_trace) == 2


async def test_list_for_owner_since_tails_new_events(store: DataStore) -> None:
    await _owner(store, "owner-tail")
    await store.events.record_event(
        event_type="owner.updated",
        owner_id="owner-tail",
        subject_type="owner",
        subject_id="owner-tail",
        actor_type="admin",
    )
    first = await store.events.list_for_owner_since("owner-tail")
    assert [e.event_type for e in first] == ["owner.updated"]
    cursor = first[-1].created_at

    # Nothing strictly after the cursor yet.
    assert await store.events.list_for_owner_since("owner-tail", after=cursor) == []

    await asyncio.sleep(0.01)  # guarantee a strictly later created_at
    await store.events.record_event(
        event_type="owner.archived",
        owner_id="owner-tail",
        subject_type="owner",
        subject_id="owner-tail",
        actor_type="admin",
    )
    tail = await store.events.list_for_owner_since("owner-tail", after=cursor)
    assert [e.event_type for e in tail] == ["owner.archived"]
