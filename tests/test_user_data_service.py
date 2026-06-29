from __future__ import annotations

from sqlalchemy import func, select

from eidolon_data import DataSettings, DataStore
from eidolon_data.schema.models import (
    CompanionRow,
    ConversationRow,
    DeviceRow,
    EventRow,
    JobRow,
    MemoryRealmRow,
    MessageRow,
    PersonaGenomeRow,
    TurnRow,
)


async def test_delete_owner_data_removes_owned_rows_and_retires_companions(tmp_path) -> None:
    store = DataStore.open(DataSettings(sqlite_path=str(tmp_path / "eidolon_data.sqlite3")))
    await store.init_schema()
    try:
        await store.owners.create(owner_id="owner-a", display_name="Owner A")
        await store.companions.create(companion_id="companion-a", owner_id="owner-a")
        await store.persona_repo.create_genome(
            genome_id="genome-a",
            companion_id="companion-a",
            version=1,
            genome_json={"identity": {"name": "Eidolon"}},
        )
        await store.companions.set_current_genome("companion-a", "genome-a")

        await store.devices.create_device(
            device_id="device-a",
            owner_id="owner-a",
            bound_companion_id="companion-a",
            auth_type="token",
            secret_ref="secret://device-a",
            access_policy_json={"role": "chat"},
        )

        await store.conversations.create_conversation(
            conversation_id="conversation-a",
            owner_id="owner-a",
            companion_id="companion-a",
            source_device_id="device-a",
        )
        await store.conversations.append_turn(
            turn_id="turn-a",
            conversation_id="conversation-a",
            seq=1,
        )
        await store.conversations.append_message(
            message_id="message-a",
            turn_id="turn-a",
            role="user",
            content="hello",
        )

        await store.memory_repo.create_realm(
            realm_id="realm-a",
            owner_id="owner-a",
            companion_id="companion-a",
        )
        await store.companions.set_default_memory_realm("companion-a", "realm-a")

        await store.jobs.create(
            job_id="job-a",
            owner_id="owner-a",
            provider="mementos",
            kind="writing",
            turn_id="turn-a",
        )
        await store.events.append(
            event_id="event-a",
            owner_id="owner-a",
            subject_type="persona",
            subject_id="companion-a",
            event_type="persona.evolution.applied",
        )

        counts = await store.owner_data_ops.delete_owner_data("owner-a")

        assert counts == {
            "messages": 1,
            "turns": 1,
            "conversations": 1,
            "memory_realms": 1,
            "jobs": 1,
            "devices": 1,
            "events": 1,
            "persona_genomes": 1,
            "companions_marked_deleted": 1,
        }

        async with store.session_factory() as session:
            for model in (
                ConversationRow,
                TurnRow,
                MessageRow,
                PersonaGenomeRow,
                MemoryRealmRow,
                JobRow,
                DeviceRow,
                EventRow,
            ):
                count = await session.scalar(select(func.count()).select_from(model))
                assert count == 0

            companion = await session.get(CompanionRow, "companion-a")
            assert companion is not None
            assert companion.status == "deleted"
            assert companion.current_genome_id is None
            assert companion.default_memory_realm_id is None
    finally:
        await store.close()
