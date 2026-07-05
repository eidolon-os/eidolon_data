"""L3 (data subproject) — real emit paths write events with the full contract.

Complements test_repositories (which checks event_type sequences) by asserting
the Phase-1 classification/correlation columns are populated through the actual
provisioning and persona services — and that the persona_genome drift is gone.
"""

from __future__ import annotations

from eidolon_data import DataStore
from eidolon_data.testing import assert_event


async def test_provision_workspace_emits_contract_fields(store: DataStore) -> None:
    await store.owner_service.create_owner(owner_id="owner-emit", display_name="Owner")
    result = await store.workspace_provisioning.provision_workspace(
        owner_id="owner-emit",
        companion_display_name="Yi",
        genome_json={"identity": {"name": "Yi"}},
    )
    cid = result.companion.companion_id
    events = await store.events.list_for_owner("owner-emit", limit=20)

    owner_ev = assert_event(events, event_type="owner.created", source="data")
    assert owner_ev.event_class == "audit"
    assert owner_ev.outcome == "success"
    assert owner_ev.occurred_at is not None

    # companion / genome / realm / workspace events carry the companion scope.
    for event_type in (
        "companion.created",
        "persona_genome.created",
        "memory_realm.created",
        "companion.workspace.initialized",
    ):
        ev = assert_event(events, event_type=event_type, source="data")
        assert ev.companion_id == cid, f"{event_type} should be companion-scoped"
        assert ev.event_class == "audit"


async def test_persona_create_genome_uses_unified_type(store: DataStore) -> None:
    await store.owner_service.create_owner(owner_id="owner-pg", display_name="Owner")
    result = await store.workspace_provisioning.provision_workspace(
        owner_id="owner-pg", companion_display_name="Yi"
    )
    cid = result.companion.companion_id

    await store.persona.create_genome(
        genome_id="g-manual-1",
        companion_id=cid,
        owner_id="owner-pg",
        event_id="evt_test_pg",
        version=2,
        genome_json={"identity": {"name": "Yi"}},
    )

    events = await store.events.list_for_subject(subject_type="companion", subject_id=cid)
    types = {e.event_type for e in events}
    # Drift reconciled: the legacy dotted name must not be emitted anymore.
    assert "persona.genome.created" not in types
    ev = assert_event(events, event_type="persona_genome.created", event_id="evt_test_pg")
    assert ev.companion_id == cid
    assert ev.source == "data"
