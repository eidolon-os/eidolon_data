"""L2 — record_event facade unit tests (see docs §14).

Verifies the contract-carrying facade: catalog-driven defaults, id format,
validation (unknown type / bad enum / missing required payload), idempotency
(deterministic id → single row), and layered-transaction behaviour
(build_event + session.add participates in the caller's transaction and rolls
back with it).
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from eidolon_data.events import build_event, new_event_id
from eidolon_data.schema.models import OwnerRow
from eidolon_data.testing import assert_event


async def _owner(store, owner_id="owner-fac"):
    async with store.session_factory() as session:
        session.add(OwnerRow(owner_id=owner_id, display_name="Fac"))
        await session.commit()
    return owner_id


def test_new_event_id_format():
    eid = new_event_id()
    assert eid.startswith("evt_") and len(eid) > 4


def test_build_event_fills_defaults_from_catalog():
    row = build_event(
        event_type="device.revoked",
        owner_id="o1",
        subject_type="device",
        subject_id="d1",
    )
    assert row.event_id.startswith("evt_")
    assert row.source == "admin"          # from spec
    assert row.event_class == "audit"     # tier from spec
    assert row.severity == "warn"         # device.revoked default
    assert row.outcome == "success"
    assert row.occurred_at is not None


def test_build_event_rejects_unregistered_type():
    with pytest.raises(ValueError, match="unregistered"):
        build_event(event_type="bogus.not.registered", owner_id="o", subject_type="x", subject_id="y")


def test_build_event_non_strict_allows_unregistered():
    row = build_event(
        event_type="bogus.not.registered",
        owner_id="o",
        subject_type="x",
        subject_id="y",
        strict=False,
    )
    assert row.source == "data" and row.event_class == "audit"  # fallback defaults


def test_build_event_validates_enums():
    with pytest.raises(ValueError, match="invalid severity"):
        build_event(
            event_type="owner.created",
            owner_id="o",
            subject_type="owner",
            subject_id="o",
            severity="loud",
        )


def test_build_event_enforces_required_payload():
    # eidolon.memory.fanout.status requires turn_id + state.
    with pytest.raises(ValueError, match="required payload"):
        build_event(
            event_type="eidolon.memory.fanout.status",
            owner_id="o",
            subject_type="turn",
            subject_id="t1",
            payload_json={"turn_id": "t1"},  # missing "state"
        )


async def test_record_event_persists_with_contract_fields(store):
    owner_id = await _owner(store)
    await store.events.record_event(
        event_type="owner.updated",
        owner_id=owner_id,
        subject_type="owner",
        subject_id=owner_id,
        actor_type="admin",
    )
    rows = await store.events.list_for_owner(owner_id)
    row = assert_event(rows, event_type="owner.updated", source="admin")
    assert row.event_class == "audit"
    assert row.actor_type == "admin"
    assert row.occurred_at is not None


async def test_record_event_idempotent_on_deterministic_id(store):
    owner_id = await _owner(store, "owner-idem")
    eid = "evt_fanout_turn9"
    kwargs = dict(
        event_type="eidolon.memory.fanout.status",
        owner_id=owner_id,
        subject_type="turn",
        subject_id="turn9",
        event_id=eid,
        payload_json={"turn_id": "turn9", "state": "published"},
    )
    await store.events.record_event(**kwargs)
    with pytest.raises(IntegrityError):  # PK collision on the deterministic id
        await store.events.record_event(**kwargs)
    rows = await store.events.list_for_subject(subject_type="turn", subject_id="turn9")
    assert len(rows) == 1


async def test_layered_tx_event_rolls_back_with_caller(store):
    owner_id = await _owner(store, "owner-tx")
    # Same-transaction emit: if the caller's tx fails, the event must not persist.
    try:
        async with store.session_factory() as session:
            session.add(
                build_event(
                    event_type="companion.created",
                    owner_id=owner_id,
                    subject_type="companion",
                    subject_id="c-tx",
                )
            )
            await session.flush()
            raise RuntimeError("caller aborts")
    except RuntimeError:
        pass
    rows = await store.events.list_for_subject(subject_type="companion", subject_id="c-tx")
    assert rows == []
