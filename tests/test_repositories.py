from __future__ import annotations

from datetime import datetime, timezone

import pytest

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
        genome_json={"identity": {"name": "Xiaoyi"}},
        prompt_markdown="# Xiaoyi\n",
    )
    assert genome.genome_json["identity"]["name"] == "Xiaoyi"
    assert genome.prompt_markdown == "# Xiaoyi\n"
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

    conversation = await store.conversations.create_conversation(
        conversation_id="conversation-1",
        owner_id=owner.owner_id,
        companion_id=companion.companion_id,
        device_id=device.device_id,
    )
    turn = await store.conversations.append_turn(
        turn_id="turn-1",
        conversation_id=conversation.conversation_id,
        seq=1,
        device_id=device.device_id,
        finished_at=datetime.now(timezone.utc),
        metrics_json={"tokens_in": 3},
    )
    assert turn.device_id == device.device_id
    await store.conversations.append_message(
        message_id="message-1",
        turn_id=turn.turn_id,
        seq=0,
        role="user",
        content="hello",
    )
    messages = await store.conversations.list_messages_for_conversation(conversation.conversation_id)
    assert [message.content for message in messages] == ["hello"]
    assert [message.seq for message in messages] == [0]

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

    job = await store.jobs.create(
        job_id="job-1",
        owner_id=owner.owner_id,
        companion_id=companion.companion_id,
        provider="mementos",
        kind="research",
        input_json={"task": "summarize"},
    )
    await store.jobs.complete(
        job.job_id,
        result_json={"ok": True},
        completed_at=datetime.now(timezone.utc),
    )

    await store.events.append(
        event_id="event-job-1",
        owner_id=owner.owner_id,
        subject_type="job",
        subject_id=job.job_id,
        event_type="job.completed",
        payload_json={"ok": True},
    )
    events = await store.events.list_for_subject(subject_type="job", subject_id=job.job_id)
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

    result = await store.maintenance.delete_owner_tree("owner-default")

    assert result.deleted is True
    assert result.devices == 1
    assert result.companions == 1
    assert result.events == 1
    assert await store.owners.get("owner-default") is None
    assert await store.devices.get_device("device-default") is None
    assert await store.owners.get("owner-keep") is not None
    assert await store.devices.get_device("device-keep") is not None


async def test_companion_workspace_initialization_is_atomic(store: DataStore) -> None:
    await store.owner_service.create_owner(owner_id="owner-workspace", display_name="Owner")

    result = await store.companion_workspace.initialize_workspace(
        owner_id="owner-workspace",
        companion_display_name="Xiaoyi",
        genome_json={"identity": {"name": "Xiaoyi"}},
        memory_policy_json={"scope": "owner"},
    )

    assert result.companion.companion_id == "c:owner-workspace:default"
    assert result.persona_genome.genome_id == "g:owner-workspace:default:v1"
    assert result.persona_genome.status == "committed"
    assert "# Xiaoyi" in result.persona_genome.prompt_markdown
    assert result.memory_realm.realm_id == "r:owner-workspace:default"

    companion = await store.companions.get(result.companion.companion_id)
    assert companion is not None
    assert companion.current_genome_id == result.persona_genome.genome_id
    assert companion.default_memory_realm_id == result.memory_realm.realm_id

    events = await store.events.list_for_owner("owner-workspace", limit=10)
    assert {
        "owner.created",
        "companion.created",
        "persona_genome.created",
        "memory_realm.created",
        "companion.workspace.initialized",
    }.issubset({event.event_type for event in events})

    second = await store.companion_workspace.initialize_workspace(
        owner_id="owner-workspace",
        companion_id="c:owner-workspace:study",
        genome_id="g:owner-workspace:study:v1",
        realm_id="r:owner-workspace:study",
        companion_display_name="Study Companion",
    )
    assert second.companion.owner_id == "owner-workspace"
    assert len(await store.companions.list_for_owner("owner-workspace")) == 2

    with pytest.raises(ValueError, match="already"):
        await store.companion_workspace.initialize_workspace(
            owner_id="owner-workspace",
            companion_id=result.companion.companion_id,
            genome_id="g:owner-workspace:duplicate:v1",
            realm_id="r:owner-workspace:duplicate",
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


async def test_conversation_requires_device_bound_to_same_companion(store: DataStore) -> None:
    await store.owners.create(owner_id="owner-conv", display_name="Owner")
    await store.companions.create(companion_id="companion-a", owner_id="owner-conv")
    await store.companions.create(companion_id="companion-b", owner_id="owner-conv")
    await store.devices.create_device(
        device_id="device-conv",
        owner_id="owner-conv",
        bound_companion_id="companion-a",
    )

    with pytest.raises(ValueError, match="is bound to companion"):
        await store.conversations.create_conversation(
            conversation_id="conversation-bad",
            owner_id="owner-conv",
            companion_id="companion-b",
            device_id="device-conv",
        )

    conversation = await store.conversations.create_conversation(
        conversation_id="conversation-ok",
        owner_id="owner-conv",
        companion_id="companion-a",
        device_id="device-conv",
    )
    assert conversation.companion_id == "companion-a"


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
    initial = await store.companion_workspace.initialize_workspace(
        owner_id="owner-evolve",
        companion_display_name="Evo",
    )

    proposal = await store.persona.create_genome_proposal(
        genome_id="g:owner-evolve:proposal:v2",
        owner_id="owner-evolve",
        companion_id=initial.companion.companion_id,
        base_genome_id=initial.persona_genome.genome_id,
        genome_json={"identity": {"name": "Evo"}},
        prompt_markdown="# Evo v2\n",
        change_summary="More concise",
    )
    assert proposal.status == "proposed"
    assert proposal.version == 2

    activated = await store.persona.activate_genome(
        owner_id="owner-evolve",
        companion_id=initial.companion.companion_id,
        genome_id=proposal.genome_id,
        expected_base_genome_id=initial.persona_genome.genome_id,
    )
    assert activated.status == "committed"
    companion = await store.companions.get(initial.companion.companion_id)
    assert companion.current_genome_id == proposal.genome_id
    assert (await store.persona.get_current_genome(initial.companion.companion_id)).genome_id == proposal.genome_id


async def test_stale_persona_genome_proposal_does_not_replace_current_genome(store: DataStore) -> None:
    await store.owner_service.create_owner(owner_id="owner-stale", display_name="Owner")
    initial = await store.companion_workspace.initialize_workspace(
        owner_id="owner-stale",
        companion_display_name="Evo",
    )
    companion_id = initial.companion.companion_id

    proposal = await store.persona.create_genome_proposal(
        genome_id="g:owner-stale:proposal:v2",
        owner_id="owner-stale",
        companion_id=companion_id,
        base_genome_id=initial.persona_genome.genome_id,
        genome_json={"identity": {"name": "Evo"}},
        prompt_markdown="# Old proposal\n",
    )
    current = await store.persona.create_genome(
        genome_id="g:owner-stale:current:v3",
        owner_id="owner-stale",
        companion_id=companion_id,
        event_id="event-current-genome",
        version=3,
        genome_json={"identity": {"name": "Evo"}},
        prompt_markdown="# Current genome\n",
    )

    with pytest.raises(PersonaGenomeConflict):
        await store.persona.activate_genome(
            owner_id="owner-stale",
            companion_id=companion_id,
            genome_id=proposal.genome_id,
            expected_base_genome_id=initial.persona_genome.genome_id,
        )

    stale = await store.persona_repo.get_genome(proposal.genome_id)
    companion = await store.companions.get(companion_id)
    assert stale.status == "stale"
    assert companion.current_genome_id == current.genome_id
