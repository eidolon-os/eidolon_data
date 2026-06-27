from __future__ import annotations

from datetime import datetime, timezone

import pytest

from eidolon_data import DataSettings, DataStore
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
    )
    assert genome.genome_json["identity"]["name"] == "Xiaoyi"
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
