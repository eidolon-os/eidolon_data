"""Tests for CompanionDeletionService and master promotion/ensure helpers."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from eidolon_data.schema.models import (
    BodyCommandRow,
    CompanionRow,
    ConversationRow,
    DeviceRow,
    EventRow,
    JobRow,
    MemoryRealmRow,
    MessageRow,
    PersonaGenomeRow,
    RuntimeCallerRow,
    RuntimeSessionRow,
    TurnRow,
)
from eidolon_data.services import CompanionDeletionError, OwnerWorkspaceError


async def _make_owner_with_two_companions(store):
    await store.owner_service.create_owner(owner_id="o1", display_name="O1")
    await store.workspace_provisioning.provision_workspace(
        owner_id="o1",
        companion_id="c_master",
        genome_id="g_master",
        realm_id="r_master",
        is_master=True,
    )
    await store.workspace_provisioning.provision_workspace(
        owner_id="o1",
        companion_id="c_side",
        genome_id="g_side",
        realm_id="r_side",
        is_master=False,
    )


async def _seed_side_references(store):
    """Add one row in every table that references companion ``c_side``."""
    async with store.session_factory() as session, session.begin():
        session.add(
            DeviceRow(
                device_id="dev-side",
                owner_id="o1",
                kind="esp32",
                status="active",
                bound_companion_id="c_side",
            )
        )
        session.add(
            RuntimeCallerRow(
                caller_id="rc-side",
                owner_id="o1",
                companion_id="c_side",
                actor_kind="user",
                actor_id="u1",
            )
        )
        session.add(
            RuntimeSessionRow(
                session_id="sess-side",
                owner_id="o1",
                companion_id="c_side",
                runtime_caller_id="rc-side",
            )
        )
        session.add(
            ConversationRow(conversation_id="conv-side", owner_id="o1", companion_id="c_side")
        )
        session.add(TurnRow(turn_id="turn-side", conversation_id="conv-side", seq=1))
        session.add(
            MessageRow(message_id="msg-side", turn_id="turn-side", seq=1, role="user", content="hi")
        )
        session.add(
            BodyCommandRow(
                command_id="bc-side", owner_id="o1", companion_id="c_side", device_id="dev-side"
            )
        )
        session.add(
            JobRow(job_id="job-side", owner_id="o1", companion_id="c_side", provider="p", kind="k")
        )
        session.add(
            EventRow(
                event_id="evt-side",
                owner_id="o1",
                companion_id="c_side",
                subject_type="companion",
                subject_id="c_side",
                event_type="companion.updated",
            )
        )


async def _count(store, model, **filters):
    async with store.session_factory() as session:
        stmt = select(model)
        for col, val in filters.items():
            stmt = stmt.where(getattr(model, col) == val)
        return len(list(await session.scalars(stmt)))


async def test_delete_non_master_removes_all_references(store):
    await _make_owner_with_two_companions(store)
    await _seed_side_references(store)

    result = await store.companion_deletion.delete_companion(
        owner_id="o1", companion_id="c_side"
    )

    assert result.deleted is True
    assert result.realm_ids == ["r_side"]
    assert "dev-side" in result.device_ids
    # Every referencing row for c_side is gone.
    assert await _count(store, CompanionRow, companion_id="c_side") == 0
    assert await _count(store, PersonaGenomeRow, companion_id="c_side") == 0
    assert await _count(store, MemoryRealmRow, companion_id="c_side") == 0
    assert await _count(store, DeviceRow, bound_companion_id="c_side") == 0
    assert await _count(store, RuntimeCallerRow, companion_id="c_side") == 0
    assert await _count(store, RuntimeSessionRow, companion_id="c_side") == 0
    assert await _count(store, BodyCommandRow, companion_id="c_side") == 0
    assert await _count(store, JobRow, companion_id="c_side") == 0
    assert await _count(store, EventRow, companion_id="c_side") == 0
    assert await _count(store, ConversationRow, companion_id="c_side") == 0
    assert await _count(store, TurnRow, conversation_id="conv-side") == 0
    assert await _count(store, MessageRow, turn_id="turn-side") == 0

    # The master companion and its rows are untouched.
    assert await _count(store, CompanionRow, companion_id="c_master") == 1
    assert await _count(store, MemoryRealmRow, companion_id="c_master") == 1


async def test_delete_master_is_refused_without_override(store):
    await _make_owner_with_two_companions(store)
    with pytest.raises(CompanionDeletionError):
        await store.companion_deletion.delete_companion(owner_id="o1", companion_id="c_master")
    assert await _count(store, CompanionRow, companion_id="c_master") == 1

    # Explicit override deletes it.
    result = await store.companion_deletion.delete_companion(
        owner_id="o1", companion_id="c_master", allow_master=True
    )
    assert result.deleted is True
    assert await _count(store, CompanionRow, companion_id="c_master") == 0


async def test_delete_unknown_companion_raises(store):
    await store.owner_service.create_owner(owner_id="o1", display_name="O1")
    with pytest.raises(CompanionDeletionError):
        await store.companion_deletion.delete_companion(owner_id="o1", companion_id="nope")


async def test_promote_to_master_ensures_realm_and_web_body(store):
    await store.owner_service.create_owner(owner_id="o2", display_name="O2")
    # A non-master companion with a genome but (after we drop it) no realm and
    # no web body — mirrors the benchmark cleanup baseline.
    await store.workspace_provisioning.provision_workspace(
        owner_id="o2",
        companion_id="c2",
        genome_id="g2",
        realm_id="r2",
        is_master=False,
    )
    async with store.session_factory() as session, session.begin():
        await session.execute(
            MemoryRealmRow.__table__.delete().where(MemoryRealmRow.companion_id == "c2")
        )
        c2 = await session.get(CompanionRow, "c2")
        c2.default_memory_realm_id = None

    row = await store.workspace_provisioning.promote_to_master(owner_id="o2", companion_id="c2")

    assert row.is_master is True
    assert row.companion_type == "master"
    assert row.current_genome_id == "g2"
    assert row.default_memory_realm_id  # a realm was (re)created
    assert await _count(store, MemoryRealmRow, companion_id="c2") == 1
    # A host-local web body now exists.
    assert await _count(store, DeviceRow, bound_companion_id="c2", kind="web") == 1


async def test_promote_demotes_previous_master(store):
    await _make_owner_with_two_companions(store)
    await store.workspace_provisioning.promote_to_master(owner_id="o1", companion_id="c_side")
    async with store.session_factory() as session:
        masters = list(
            await session.scalars(
                select(CompanionRow.companion_id)
                .where(CompanionRow.owner_id == "o1")
                .where(CompanionRow.is_master.is_(True))
            )
        )
    assert masters == ["c_side"]
    async with store.session_factory() as session:
        rows = list(
            await session.scalars(
                select(CompanionRow)
                .where(CompanionRow.owner_id == "o1")
                .order_by(CompanionRow.companion_id)
            )
        )
    assert [(row.companion_id, row.is_master, row.companion_type) for row in rows] == [
        ("c_master", False, "slave"),
        ("c_side", True, "master"),
    ]


async def test_ensure_memory_realm_is_idempotent(store):
    await store.owner_service.create_owner(owner_id="o3", display_name="O3")
    await store.workspace_provisioning.provision_workspace(
        owner_id="o3", companion_id="c3", genome_id="g3", realm_id="r3", is_master=False
    )
    first = await store.workspace_provisioning.ensure_memory_realm(owner_id="o3", companion_id="c3")
    second = await store.workspace_provisioning.ensure_memory_realm(owner_id="o3", companion_id="c3")
    assert first.realm_id == second.realm_id == "r3"
    assert await _count(store, MemoryRealmRow, companion_id="c3") == 1


async def test_promote_unknown_companion_raises(store):
    await store.owner_service.create_owner(owner_id="o4", display_name="O4")
    with pytest.raises(OwnerWorkspaceError):
        await store.workspace_provisioning.promote_to_master(owner_id="o4", companion_id="nope")
