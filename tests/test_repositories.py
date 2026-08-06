from __future__ import annotations

import pytest
from eidolon_sdk.biz.persona import (
    PersonaEvidenceRef,
    PersonaEvolutionProposalEvent,
    build_default_persona_genome,
    persona_genome_to_json,
)

from eidolon_data import DataSettings, DataStore
from eidolon_data.repositories.persona import PersonaGenomeConflict
from eidolon_data.schema.types import (
    MemoryEngineHealth,
    MemoryIngestResult,
    MemoryItem,
    RecallResult,
)


class FakeMemoryEngine:
    async def ingest(self, item: MemoryItem) -> MemoryIngestResult:
        return MemoryIngestResult(
            memory_id=item.memory_id,
            realm_id=item.realm_id,
            engine="fake",
            external_key=f"fake://{item.realm_id}/{item.memory_id}",
            metadata={"source": "test"},
        )

    async def recall(self, realm_id: str, query: str, options) -> RecallResult:
        return RecallResult(metadata={"realm_id": realm_id, "query": query, "top_k": options.top_k})

    async def delete(self, memory_id: str) -> None:
        return None

    async def health(self) -> MemoryEngineHealth:
        return MemoryEngineHealth(ok=True, engine="fake")


def _genome(name: str, *, base_genome_id: str | None = None) -> dict:
    return persona_genome_to_json(
        build_default_persona_genome(name=name, base_genome_id=base_genome_id)
    )


def _proposal(
    *, owner_id: str, companion_id: str, base, genome_id: str
) -> PersonaEvolutionProposalEvent:
    return PersonaEvolutionProposalEvent(
        proposal_id=f"proposal-{genome_id}",
        owner_id=owner_id,
        companion_id=companion_id,
        base_genome_id=base.genome_id,
        base_genome_hash=base.genome_hash,
        proposed_genome_id=genome_id,
        rationale="Prefer concise responses based on repeated evidence.",
        proposed_genome=build_default_persona_genome(
            name="Evo",
            origin="memory_reflection",
            base_genome_id=base.genome_id,
        ),
        evidence_refs=[
            PersonaEvidenceRef(
                kind="memory_fragment",
                ref_id="memory-evidence",
                summary="Owner repeatedly asked for more concise responses.",
                confidence=0.9,
            )
        ],
    )


@pytest.fixture
async def store(tmp_path):
    data_store = DataStore.open(
        DataSettings(sqlite_path=str(tmp_path / "eidolon_data.sqlite3")),
        memory_engine=FakeMemoryEngine(),
    )
    await data_store.init_schema()
    try:
        yield data_store
    finally:
        await data_store.close()


async def test_repository_workflow_round_trips_core_entities(store: DataStore) -> None:
    owner = await store.owners.create(owner_id="owner-1", display_name="Manson")
    companion = await store.companions.create(
        companion_id="companion-1",
        owner_id=owner.owner_id,
        display_name="Xiaoyi",
        profile_json={"voice": "warm"},
    )
    genome = await store.persona.create_genome(
        genome_id="genome-1",
        companion_id=companion.companion_id,
        owner_id=owner.owner_id,
        event_id="event-persona-1",
        source_json={
            "source_type": "template",
            "template_id": "warm-companion",
            "template_revision": 1,
        },
        genome_json=_genome("Xiaoyi"),
    )
    assert genome.genome_json["constitution"]["name"] == "Xiaoyi"
    assert genome.schema_version == "eidolon.persona_genome"
    assert genome.genome_hash.startswith("pg_")
    assert genome.status == "committed"
    assert genome.source_json["template_id"] == "warm-companion"
    assert (await store.companions.get(companion.companion_id)).current_genome_id == "genome-1"

    device = await store.devices.create_device(
        device_id="device-1",
        owner_id=owner.owner_id,
        kind="voice_body",
        bound_companion_id=companion.companion_id,
        interaction_mode="voice",
        auth_type="psk",
        secret_ref="secret://device-1",
        capabilities_json={"audio": True},
        access_policy_json={"capability": "speak"},
    )
    assert device.bound_companion_id == companion.companion_id
    assert device.auth_type == "psk"
    assert device.secret_ref == "secret://device-1"
    assert device.access_policy_json["capability"] == "speak"

    command = await store.body_commands.upsert_command(
        command_id="command-1",
        owner_id=owner.owner_id,
        companion_id=companion.companion_id,
        # Runtime session ids are opaque correlation values in System Data;
        # their authority lives in eidolon-agent.sqlite3.
        runtime_session_id="session-1",
        device_id=device.device_id,
        source_device_id=device.device_id,
        topic="eidolon.control",
        op="device.identify",
        status="sent",
        payload_json={"prompt": "identify"},
        envelope_json={"id": "command-1"},
    )
    assert command.status == "sent"
    assert command.runtime_session_id == "session-1"
    updated_command = await store.body_commands.update_status(
        "command-1",
        status="accepted",
        ack_json={"code": "OK"},
        result_json=True,
    )
    assert updated_command is not None
    assert updated_command.ack_json["code"] == "OK"
    stored_command = await store.body_commands.get_command("command-1")
    assert stored_command is not None
    assert stored_command.status == "accepted"
    assert stored_command.result_json is True
    assert [
        item.command_id for item in await store.body_commands.list_for_device(device.device_id)
    ] == ["command-1"]

    realm = await store.memory_repo.create_realm(
        realm_id="realm-1",
        owner_id=owner.owner_id,
        companion_id=companion.companion_id,
        policy_json={"recall": "owner"},
    )
    await store.companions.set_default_memory_realm(companion.companion_id, realm.realm_id)
    result = await store.memory.ingest_item(
        memory_id="memory-1",
        realm_id=realm.realm_id,
        item_type="preference",
        content_summary="likes tea",
        payload_json={"value": "tea"},
    )
    assert result.memory_id == "memory-1"
    assert result.external_key == "fake://realm-1/memory-1"

    await store.events.append(
        event_id="event-job-1",
        owner_id=owner.owner_id,
        subject_type="job",
        subject_id="job-1",
        event_type="job.completed",
        payload_json={"ok": True},
    )
    events = await store.events.list_for_subject(subject_type="job", subject_id="job-1")
    assert [event.event_type for event in events] == ["job.completed"]


async def test_owner_service_creates_owner_without_workspace(store: DataStore) -> None:
    result = await store.owner_service.create_owner(
        owner_id="owner-service",
        display_name="Owner Service",
    )

    assert result.owner.status == "active"
    assert await store.companions.list_for_owner("owner-service") == []
    assert await store.memory_repo.list_realms_for_owner("owner-service") == []

    events = await store.events.list_for_subject(
        subject_type="owner",
        subject_id="owner-service",
    )
    assert [event.event_type for event in events] == ["owner.created"]


async def test_maintenance_deletes_owner_default_tree_only(store: DataStore) -> None:
    await store.owners.create(owner_id="owner-default", display_name="Default")
    await store.owners.create(owner_id="owner-keep", display_name="Keep")
    await store.companions.create(
        companion_id="companion-default",
        owner_id="owner-default",
        display_name="Default Companion",
    )
    await store.devices.create_device(
        device_id="device-default",
        owner_id="owner-default",
        kind="esp32",
    )
    await store.devices.create_device(
        device_id="device-keep",
        owner_id="owner-keep",
        kind="esp32",
    )
    await store.events.append(
        event_id="event-default",
        owner_id="owner-default",
        subject_type="device",
        subject_id="device-default",
        event_type="device.discovered",
    )

    result = await store.dev_maintenance.delete_owner_tree("owner-default")

    assert result.deleted is True
    assert result.devices == 1
    assert result.companions == 1
    assert result.events == 0
    assert await store.owners.get("owner-default") is None
    assert await store.devices.get_device("device-default") is None
    assert await store.owners.get("owner-keep") is not None
    assert await store.devices.get_device("device-keep") is not None


async def test_companion_workspace_initialization_is_atomic(store: DataStore) -> None:
    await store.owner_service.create_owner(owner_id="owner-workspace", display_name="Owner")

    result = await store.workspace_provisioning.provision_workspace(
        owner_id="owner-workspace",
        companion_display_name="Xiaoyi",
        genome_json=_genome("Xiaoyi"),
        memory_policy_json={"scope": "owner"},
    )

    assert result.companion.companion_id == "c_owner-workspace_default"
    assert result.persona_genome.genome_id == "g_owner-workspace_default"
    assert result.persona_genome.status == "committed"
    assert result.persona_genome.applied_event_id is not None
    assert result.persona_genome.genome_json["constitution"]["name"] == "Xiaoyi"
    assert result.memory_realm.realm_id == "r_owner-workspace_default"

    companion = await store.companions.get(result.companion.companion_id)
    assert companion is not None
    assert companion.current_genome_id == result.persona_genome.genome_id
    assert companion.default_memory_realm_id == result.memory_realm.realm_id

    events = await store.events.list_for_owner("owner-workspace", limit=10)
    assert {event.event_type for event in events} == {
        "owner.created",
        "companion.workspace.initialized",
    }

    second = await store.workspace_provisioning.provision_workspace(
        owner_id="owner-workspace",
        companion_id="c_owner-workspace_study",
        genome_id="g_owner-workspace_study",
        realm_id="r_owner-workspace_study",
        companion_display_name="Study Companion",
    )
    assert second.companion.owner_id == "owner-workspace"
    assert len(await store.companions.list_for_owner("owner-workspace")) == 2

    with pytest.raises(ValueError, match="already"):
        await store.workspace_provisioning.provision_workspace(
            owner_id="owner-workspace",
            companion_id=result.companion.companion_id,
            genome_id="g_owner-workspace_duplicate",
            realm_id="r_owner-workspace_duplicate",
        )


async def test_workspace_provisioning_rejects_legacy_colon_ids(
    store: DataStore,
) -> None:
    await store.owner_service.create_owner(owner_id="owner-migrate", display_name="Owner")

    with pytest.raises(ValueError, match="companion_id"):
        await store.workspace_provisioning.provision_workspace(
            owner_id="owner-migrate",
            companion_id="c:owner-migrate:default",
            genome_id="g_owner-migrate_default_v1",
            realm_id="r_owner-migrate_default",
        )


async def test_device_binding_requires_same_owner_active_companion(store: DataStore) -> None:
    await store.owners.create(owner_id="owner-a", display_name="Owner A")
    await store.owners.create(owner_id="owner-b", display_name="Owner B")
    await store.companions.create(companion_id="companion-a", owner_id="owner-a")
    await store.companions.create(companion_id="companion-b", owner_id="owner-b")
    await store.devices.create_device(device_id="device-1", owner_id=None, kind="voice")

    with pytest.raises(ValueError, match="belongs to owner"):
        await store.devices.claim_device(
            "device-1",
            owner_id="owner-a",
            companion_id="companion-b",
        )

    device = await store.devices.claim_device(
        "device-1",
        owner_id="owner-a",
        companion_id="companion-a",
    )
    assert device.owner_id == "owner-a"
    assert device.bound_companion_id == "companion-a"


async def test_companion_can_bind_multiple_bodies(store: DataStore) -> None:
    await store.owners.create(owner_id="owner-multi", display_name="Owner")
    await store.companions.create(companion_id="companion-multi", owner_id="owner-multi")

    await store.devices.create_device(
        device_id="web-companion-multi",
        owner_id="owner-multi",
        kind="web",
        bound_companion_id="companion-multi",
    )
    # A second, physical body on the same companion must now be allowed.
    await store.devices.create_device(
        device_id="esp-companion-multi",
        owner_id="owner-multi",
        kind="esp32",
        bound_companion_id="companion-multi",
    )

    bodies = await store.devices.list_devices_for_companion("companion-multi")
    assert {d.device_id for d in bodies} == {"web-companion-multi", "esp-companion-multi"}


async def test_provision_master_does_not_implicitly_create_a_device(store: DataStore) -> None:
    await store.owner_service.create_owner(owner_id="owner-master", display_name="Owner")

    result = await store.workspace_provisioning.provision_workspace(
        owner_id="owner-master",
        companion_display_name="Xiaoyi",
        is_master=True,
    )
    assert result.companion.is_master is True

    assert await store.devices.list_devices_for_companion(result.companion.companion_id) == []

    events = await store.events.list_for_owner("owner-master", limit=20)
    assert "device.web_body.provisioned" not in {event.event_type for event in events}

    # A non-master companion provisions no web body.
    non_master = await store.workspace_provisioning.provision_workspace(
        owner_id="owner-master",
        companion_id="c_owner-master_study",
        genome_id="g_owner-master_study_v1",
        realm_id="r_owner-master_study",
        companion_display_name="Study",
    )
    assert non_master.companion.is_master is False
    assert await store.devices.list_devices_for_companion(non_master.companion.companion_id) == []


async def test_ensure_web_body_is_idempotent(store: DataStore) -> None:
    await store.owners.create(owner_id="owner-ewb", display_name="Owner")
    await store.companions.create(
        companion_id="companion-ewb",
        owner_id="owner-ewb",
        display_name="Companion",
    )

    first = await store.workspace_provisioning.ensure_web_body(
        owner_id="owner-ewb", companion_id="companion-ewb"
    )
    second = await store.workspace_provisioning.ensure_web_body(
        owner_id="owner-ewb", companion_id="companion-ewb"
    )

    assert first.device_id == second.device_id
    web_bodies = [
        d
        for d in await store.devices.list_devices_for_companion("companion-ewb")
        if d.kind == "web"
    ]
    assert len(web_bodies) == 1


async def test_promote_to_master_does_not_implicitly_create_a_device(store: DataStore) -> None:
    await store.owners.create(owner_id="owner-promote", display_name="Owner")
    await store.companions.create(
        companion_id="companion-promote",
        owner_id="owner-promote",
        display_name="Companion",
    )

    promoted = await store.workspace_provisioning.promote_to_master(
        owner_id="owner-promote",
        companion_id="companion-promote",
    )

    assert promoted.is_master is True
    assert await store.devices.list_devices_for_companion("companion-promote") == []


async def test_default_memory_realm_must_belong_to_companion(store: DataStore) -> None:
    await store.owners.create(owner_id="owner-realm", display_name="Owner")
    await store.companions.create(companion_id="companion-a", owner_id="owner-realm")
    await store.companions.create(companion_id="companion-b", owner_id="owner-realm")
    realm = await store.memory_repo.create_realm(
        realm_id="realm-a",
        owner_id="owner-realm",
        companion_id="companion-a",
    )

    with pytest.raises(ValueError, match="belongs to companion"):
        await store.companions.set_default_memory_realm("companion-b", realm.realm_id)


async def test_persona_genome_proposal_requires_current_base(store: DataStore) -> None:
    await store.owner_service.create_owner(owner_id="owner-evolve", display_name="Owner")
    initial = await store.workspace_provisioning.provision_workspace(
        owner_id="owner-evolve",
        companion_display_name="Evo",
    )

    proposal = await store.persona.create_evolution_proposal(
        _proposal(
            owner_id="owner-evolve",
            companion_id=initial.companion.companion_id,
            base=initial.persona_genome,
            genome_id="g_owner-evolve_proposal_v2",
        )
    )
    assert proposal.status == "proposed"
    assert proposal.version == 2

    activated = await store.persona.approve_evolution(
        owner_id="owner-evolve",
        companion_id=initial.companion.companion_id,
        proposed_genome_id=proposal.genome_id,
        expected_base_genome_id=initial.persona_genome.genome_id,
    )
    assert activated.status == "committed"
    companion = await store.companions.get(initial.companion.companion_id)
    assert companion.current_genome_id == proposal.genome_id
    assert (
        await store.persona.get_current_genome(initial.companion.companion_id)
    ).genome_id == proposal.genome_id


async def test_stale_persona_genome_proposal_does_not_replace_current_genome(
    store: DataStore,
) -> None:
    await store.owner_service.create_owner(owner_id="owner-stale", display_name="Owner")
    initial = await store.workspace_provisioning.provision_workspace(
        owner_id="owner-stale",
        companion_display_name="Evo",
    )
    companion_id = initial.companion.companion_id

    proposal = await store.persona.create_evolution_proposal(
        _proposal(
            owner_id="owner-stale",
            companion_id=companion_id,
            base=initial.persona_genome,
            genome_id="g_owner-stale_proposal_v2",
        )
    )
    current = await store.persona.create_genome(
        genome_id="g_owner-stale_current_v3",
        owner_id="owner-stale",
        companion_id=companion_id,
        event_id="event-current-genome",
        version=3,
        genome_json=_genome("Evo", base_genome_id=initial.persona_genome.genome_id),
    )

    with pytest.raises(PersonaGenomeConflict):
        await store.persona.approve_evolution(
            owner_id="owner-stale",
            companion_id=companion_id,
            proposed_genome_id=proposal.genome_id,
            expected_base_genome_id=initial.persona_genome.genome_id,
        )

    stale = await store.persona_repo.get_genome(proposal.genome_id)
    companion = await store.companions.get(companion_id)
    assert stale.status == "stale"
    assert companion.current_genome_id == current.genome_id


async def test_reset_to_origin_returns_to_authored_v1(store: DataStore) -> None:
    await store.owners.create(owner_id="owner-reset", display_name="Owner")
    await store.companions.create(
        companion_id="c-reset", owner_id="owner-reset", display_name="Xiaoyi"
    )
    # v1 is the authored origin (base points at itself); v2 is evolution drift.
    await store.persona.create_genome(
        genome_id="g-reset-1",
        companion_id="c-reset",
        owner_id="owner-reset",
        event_id="ev-r1",
        version=1,
        base_genome_id="g-reset-1",
        genome_json=_genome("v1", base_genome_id="g-reset-1"),
        status="committed",
    )
    await store.persona.create_genome(
        genome_id="g-reset-2",
        companion_id="c-reset",
        owner_id="owner-reset",
        event_id="ev-r2",
        version=2,
        base_genome_id="g-reset-1",
        genome_json=_genome("v2-evolved", base_genome_id="g-reset-1"),
        status="committed",
    )
    assert (await store.companions.get("c-reset")).current_genome_id == "g-reset-2"

    origin = await store.persona.reset_to_origin(owner_id="owner-reset", companion_id="c-reset")
    assert origin.genome_id == "g-reset-1"
    assert (await store.companions.get("c-reset")).current_genome_id == "g-reset-1"
