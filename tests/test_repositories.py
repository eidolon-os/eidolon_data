from __future__ import annotations

from datetime import datetime, timezone

import pytest

from eidolon_data import DataStore
from eidolon_data.repositories.persona import PersonaGenomeConflict

# The ``store`` fixture (+ FakeMemoryEngine) now lives in tests/conftest.py.


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
    assert genome.genome_json["identity_core"]["name"] == "Xiaoyi"
    assert genome.schema_version == "eidolon.persona_genome.v1"
    assert genome.genome_hash.startswith("pgv1_")
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

    caller = await store.runtime_callers.upsert_caller(
        caller_id="rc-test",
        owner_id=owner.owner_id,
        companion_id=companion.companion_id,
        actor_kind="web_chat",
        actor_id="browser-1",
        display_name="Browser",
        source_device_id=device.device_id,
    )
    assert caller.actor_kind == "web_chat"
    session = await store.runtime_sessions.upsert_session(
        session_id="session-1",
        owner_id=owner.owner_id,
        companion_id=companion.companion_id,
        runtime_caller_id=caller.caller_id,
        source_device_id=device.device_id,
        transport="grpc_chat",
    )
    assert session.runtime_caller_id == caller.caller_id

    command = await store.body_commands.upsert_command(
        command_id="command-1",
        owner_id=owner.owner_id,
        companion_id=companion.companion_id,
        runtime_caller_id=caller.caller_id,
        runtime_session_id=session.session_id,
        device_id=device.device_id,
        source_device_id=device.device_id,
        topic="eidolon.control",
        op="device.identify",
        status="sent",
        payload_json={"prompt": "identify"},
        envelope_json={"id": "command-1"},
    )
    assert command.status == "sent"
    assert command.runtime_caller_id == caller.caller_id
    assert command.runtime_session_id == session.session_id
    updated_command = await store.body_commands.update_status(
        "command-1",
        status="accepted",
        ack_json={"code": "OK"},
    )
    assert updated_command is not None
    assert updated_command.ack_json["code"] == "OK"
    stored_command = await store.body_commands.get_command("command-1")
    assert stored_command is not None
    assert stored_command.status == "accepted"
    assert [item.command_id for item in await store.body_commands.list_for_device(device.device_id)] == [
        "command-1"
    ]

    conversation = await store.conversations.create_conversation(
        conversation_id="conversation-1",
        owner_id=owner.owner_id,
        companion_id=companion.companion_id,
        runtime_caller_id=caller.caller_id,
        runtime_session_id=session.session_id,
        source_device_id=device.device_id,
    )
    turn = await store.conversations.append_turn(
        turn_id="turn-1",
        conversation_id=conversation.conversation_id,
        seq=1,
        runtime_caller_id=caller.caller_id,
        runtime_session_id=session.session_id,
        source_device_id=device.device_id,
        finished_at=datetime.now(timezone.utc),
        metrics_json={"tokens_in": 3},
    )
    assert conversation.runtime_caller_id == caller.caller_id
    assert conversation.runtime_session_id == session.session_id
    assert turn.runtime_caller_id == caller.caller_id
    assert turn.runtime_session_id == session.session_id
    assert turn.source_device_id == device.device_id
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
    workspace = await store.workspace_provisioning.provision_workspace(
        owner_id="owner-default",
        companion_id="companion-default",
        genome_id="genome-default",
        realm_id="realm-default",
        is_master=True,
    )
    companion_id = workspace.companion.companion_id
    await store.companions.create(
        companion_id="companion-keep",
        owner_id="owner-keep",
        display_name="Keep Companion",
    )
    await store.devices.create_device(
        device_id="device-default",
        owner_id="owner-default",
        kind="esp32",
        bound_companion_id=companion_id,
    )
    await store.devices.create_device(
        device_id="device-keep",
        owner_id="owner-keep",
        kind="esp32",
    )
    caller = await store.runtime_callers.upsert_caller(
        caller_id="caller-default",
        owner_id="owner-default",
        companion_id=companion_id,
        actor_kind="web",
        actor_id="browser",
        source_device_id="device-default",
    )
    session = await store.runtime_sessions.upsert_session(
        session_id="session-default",
        owner_id="owner-default",
        companion_id=companion_id,
        runtime_caller_id=caller.caller_id,
        source_device_id="device-default",
    )
    conversation = await store.conversations.create_conversation(
        conversation_id="conversation-default",
        owner_id="owner-default",
        companion_id=companion_id,
        runtime_caller_id=caller.caller_id,
        runtime_session_id=session.session_id,
        source_device_id="device-default",
    )
    turn = await store.conversations.append_turn(
        turn_id="turn-default",
        conversation_id=conversation.conversation_id,
        seq=1,
        runtime_caller_id=caller.caller_id,
        runtime_session_id=session.session_id,
        source_device_id="device-default",
    )
    await store.conversations.append_message(
        message_id="message-default",
        turn_id=turn.turn_id,
        seq=1,
        role="user",
        content="delete me",
    )
    await store.body_commands.upsert_command(
        command_id="command-default",
        owner_id=None,
        companion_id=companion_id,
        runtime_caller_id=caller.caller_id,
        runtime_session_id=session.session_id,
        device_id="device-default",
        source_device_id="device-default",
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
    assert result.devices == 2  # physical device + auto-provisioned web body
    assert result.companions == 1
    assert result.persona_genomes == 1
    assert result.memory_realms == 1
    assert result.body_commands == 1
    assert result.runtime_callers == 1
    assert result.runtime_sessions == 1
    assert result.conversations == 1
    assert result.turns == 1
    assert result.messages == 1
    assert result.events >= 1
    assert result.realm_ids == ["realm-default"]
    assert await store.owners.get("owner-default") is None
    assert await store.devices.get_device("device-default") is None
    assert await store.devices.get_device("web-companion-default") is None
    assert await store.companions.get("companion-default") is None
    assert await store.persona_repo.get_genome("genome-default") is None
    assert await store.memory_repo.get_realm("realm-default") is None
    assert await store.runtime_callers.get("caller-default") is None
    assert await store.runtime_sessions.get("session-default") is None
    assert await store.body_commands.get_command("command-default") is None
    assert await store.owners.get("owner-keep") is not None
    assert await store.devices.get_device("device-keep") is not None
    assert await store.companions.get("companion-keep") is not None


async def test_companion_workspace_initialization_is_atomic(store: DataStore) -> None:
    await store.owner_service.create_owner(owner_id="owner-workspace", display_name="Owner")

    result = await store.workspace_provisioning.provision_workspace(
        owner_id="owner-workspace",
        companion_display_name="Xiaoyi",
        genome_json={"identity": {"name": "Xiaoyi"}},
        memory_policy_json={"scope": "owner"},
    )

    assert result.companion.companion_id == "c_owner-workspace_default"
    assert result.persona_genome.genome_id == "g_owner-workspace_default_v1"
    assert result.persona_genome.status == "committed"
    assert result.persona_genome.genome_json["identity_core"]["name"] == "Xiaoyi"
    assert result.persona_genome.genome_hash.startswith("pgv1_")
    assert result.memory_realm.realm_id == "r_owner-workspace_default"

    companion = await store.companions.get(result.companion.companion_id)
    assert companion is not None
    assert companion.current_genome_id == result.persona_genome.genome_id
    assert companion.default_memory_realm_id == result.memory_realm.realm_id

    events = await store.events.list_for_owner("owner-workspace", limit=10)
    assert {
        "owner.created",
        "companion.created",
        "persona.genome.committed",
        "memory_realm.created",
        "companion.workspace.initialized",
    }.issubset({event.event_type for event in events})

    second = await store.workspace_provisioning.provision_workspace(
        owner_id="owner-workspace",
        companion_id="c_owner-workspace_study",
        genome_id="g_owner-workspace_study_v1",
        realm_id="r_owner-workspace_study",
        companion_display_name="Study Companion",
    )
    assert second.companion.owner_id == "owner-workspace"
    assert len(await store.companions.list_for_owner("owner-workspace")) == 2

    with pytest.raises(ValueError, match="already"):
        await store.workspace_provisioning.provision_workspace(
            owner_id="owner-workspace",
            companion_id=result.companion.companion_id,
            genome_id="g_owner-workspace_duplicate_v1",
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


async def test_provision_master_creates_web_body(store: DataStore) -> None:
    await store.owner_service.create_owner(owner_id="owner-master", display_name="Owner")

    result = await store.workspace_provisioning.provision_workspace(
        owner_id="owner-master",
        companion_display_name="Xiaoyi",
        is_master=True,
    )
    assert result.companion.is_master is True
    assert result.companion.companion_type == "master"

    bodies = await store.devices.list_devices_for_companion(result.companion.companion_id)
    web_bodies = [d for d in bodies if d.kind == "web"]
    assert len(web_bodies) == 1
    web = web_bodies[0]
    assert web.device_id == f"web-{result.companion.companion_id}"
    assert web.bound_companion_id == result.companion.companion_id
    assert web.status == "active"
    assert web.approved_at is not None
    assert web.approved_by == "system:onboarding"
    assert web.auth_type == "admin_trust"
    assert web.interaction_mode == "full_duplex"
    assert web.capabilities_json == {
        "audio": True,
        "display": True,
        "text": True,
        "local_web": True,
    }
    assert web.access_policy_json == {
        "conversation": True,
        "voice_input": True,
        "voice_output": True,
        "memory_recall": True,
        "body_commands": False,
    }
    assert web.metadata_json.get("role") == "local_web"
    assert web.metadata_json.get("auto_provisioned") is True
    assert web.metadata_json.get("provisioned_by") == "owner_onboarding"
    assert web.metadata_json.get("companion_type") == "master"

    events = await store.events.list_for_owner("owner-master", limit=20)
    assert "device.web_body.provisioned" in {event.event_type for event in events}

    # A non-master companion provisions no web body.
    non_master = await store.workspace_provisioning.provision_workspace(
        owner_id="owner-master",
        companion_id="c_owner-master_study",
        genome_id="g_owner-master_study_v1",
        realm_id="r_owner-master_study",
        companion_display_name="Study",
    )
    assert non_master.companion.is_master is False
    assert non_master.companion.companion_type == "slave"
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
    assert first.auth_type == "admin_trust"
    assert first.approved_by == "system:onboarding"
    assert first.access_policy_json["conversation"] is True
    assert first.metadata_json["companion_type"] == "slave"
    web_bodies = [
        d
        for d in await store.devices.list_devices_for_companion("companion-ewb")
        if d.kind == "web"
    ]
    assert len(web_bodies) == 1


async def test_conversation_source_device_is_independent_from_body_binding(store: DataStore) -> None:
    await store.owners.create(owner_id="owner-conv", display_name="Owner")
    await store.companions.create(companion_id="companion-a", owner_id="owner-conv")
    await store.companions.create(companion_id="companion-b", owner_id="owner-conv")
    await store.devices.create_device(
        device_id="device-conv",
        owner_id="owner-conv",
        bound_companion_id="companion-a",
    )

    conversation = await store.conversations.create_conversation(
        conversation_id="conversation-ok",
        owner_id="owner-conv",
        companion_id="companion-b",
        source_device_id="device-conv",
    )
    assert conversation.companion_id == "companion-b"
    assert conversation.source_device_id == "device-conv"


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

    proposal = await store.persona.create_genome_proposal(
        genome_id="g_owner-evolve_proposal_v2",
        owner_id="owner-evolve",
        companion_id=initial.companion.companion_id,
        base_genome_id=initial.persona_genome.genome_id,
        genome_json={"identity": {"name": "Evo"}},
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
    initial = await store.workspace_provisioning.provision_workspace(
        owner_id="owner-stale",
        companion_display_name="Evo",
    )
    companion_id = initial.companion.companion_id

    proposal = await store.persona.create_genome_proposal(
        genome_id="g_owner-stale_proposal_v2",
        owner_id="owner-stale",
        companion_id=companion_id,
        base_genome_id=initial.persona_genome.genome_id,
        genome_json={"identity": {"name": "Evo"}},
    )
    current = await store.persona.create_genome(
        genome_id="g_owner-stale_current_v3",
        owner_id="owner-stale",
        companion_id=companion_id,
        event_id="event-current-genome",
        version=3,
        genome_json={"identity": {"name": "Evo"}},
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


async def test_reset_to_origin_returns_to_authored_v1(store: DataStore) -> None:
    await store.owners.create(owner_id="owner-reset", display_name="Owner")
    await store.companions.create(
        companion_id="c-reset", owner_id="owner-reset", display_name="Xiaoyi"
    )
    # v1 is the authored origin (base points at itself); v2 is evolution drift.
    await store.persona.create_genome(
        genome_id="g-reset-1", companion_id="c-reset", owner_id="owner-reset",
        event_id="ev-r1", version=1, base_genome_id="g-reset-1",
        genome_json={"identity": {"name": "v1"}}, status="committed",
    )
    await store.persona.create_genome(
        genome_id="g-reset-2", companion_id="c-reset", owner_id="owner-reset",
        event_id="ev-r2", version=2, base_genome_id="g-reset-1",
        genome_json={"identity": {"name": "v2-evolved"}}, status="committed",
    )
    assert (await store.companions.get("c-reset")).current_genome_id == "g-reset-2"

    origin = await store.persona.reset_to_origin(owner_id="owner-reset", companion_id="c-reset")
    assert origin.genome_id == "g-reset-1"
    assert (await store.companions.get("c-reset")).current_genome_id == "g-reset-1"
