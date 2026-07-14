from __future__ import annotations

import pytest

from eidolon_data import DataSettings, DataStore


@pytest.fixture
async def store(tmp_path):
    value = DataStore.open(DataSettings(sqlite_path=str(tmp_path / "guard.sqlite3")))
    await value.init_schema()
    try:
        yield value
    finally:
        await value.close()


async def test_guard_bindings_are_multi_guard_and_auto_provision_isolated_workspaces(store: DataStore) -> None:
    await store.owners.create(owner_id="owner-1", display_name="Owner")
    for device_id in ("atk-1", "atk-2", "atk-3"):
        await store.devices.create_device(
            device_id=device_id,
            owner_id=None,
            kind="esp32",
            capabilities_json={"guard": {"enabled": True, "protocol_versions": [1]}},
        )

    guard = await store.guard_bindings.ensure_guard_companion(
        owner_id="owner-1",
        companion_id="guard-1",
    )
    assert guard.kind == "guard"
    assert guard.is_master is False
    assert guard.companion_type == "guard"
    assert guard.current_genome_id is not None
    assert guard.default_memory_realm_id is not None
    assert [realm.realm_id for realm in await store.memory_repo.list_realms_for_owner("owner-1")] == [
        guard.default_memory_realm_id
    ]

    first = await store.guard_bindings.claim(
        owner_id="owner-1",
        device_id="atk-1",
        guard_companion_id="guard-1",
    )
    assert first.state == "active"
    assert first.desired_runtime_state == "running"
    assert first.runtime_revision == 1
    first_deliveries = await store.guard_runtime_deliveries.list_for_binding(first.binding_id)
    assert [(row.runtime_revision, row.desired_runtime_state, row.status) for row in first_deliveries] == [
        (1, "running", "pending")
    ]
    assert (await store.devices.get_device("atk-1")).bound_companion_id == "guard-1"

    second = await store.guard_bindings.claim(
        owner_id="owner-1",
        device_id="atk-2",
        guard_companion_id="guard-2",
    )
    assert second.state == "active"
    second_guard = await store.companions.get("guard-2")
    assert second_guard is not None
    assert second_guard.companion_type == "guard"
    assert second_guard.current_genome_id != guard.current_genome_id
    assert second_guard.default_memory_realm_id != guard.default_memory_realm_id

    replacement = await store.guard_bindings.claim(
        owner_id="owner-1",
        device_id="atk-3",
        guard_companion_id="guard-1",
        replace=True,
    )
    assert replacement.state == "active"
    assert replacement.config_revision == 2
    replaced = await store.guard_bindings.get(first.binding_id)
    assert replaced.state == "replaced"
    assert replaced.desired_runtime_state == "stopped"
    assert replaced.runtime_revision == 2
    replaced_deliveries = await store.guard_runtime_deliveries.list_for_binding(first.binding_id)
    assert [(row.runtime_revision, row.desired_runtime_state) for row in replaced_deliveries] == [
        (1, "running"),
        (2, "stopped"),
    ]
    assert (await store.devices.get_device("atk-1")).owner_id is None
    assert (await store.guard_bindings.get(second.binding_id)).state == "active"


async def test_only_guard_capable_pending_devices_can_be_claimed(store: DataStore) -> None:
    await store.owners.create(owner_id="owner-1", display_name="Owner")
    await store.devices.create_device(device_id="atk-plain", owner_id=None, capabilities_json={})
    await store.devices.create_device(
        device_id="atk-guard",
        owner_id=None,
        capabilities_json={"ops": [{"name": "guard.presence.candidate"}]},
    )
    await store.guard_bindings.ensure_guard_companion(
        owner_id="owner-1", companion_id="guard-1"
    )

    assert [row.device_id for row in await store.guard_bindings.list_pending_guard_devices()] == ["atk-guard"]
    with pytest.raises(ValueError, match="not guard-capable"):
        await store.guard_bindings.claim(
            owner_id="owner-1",
            device_id="atk-plain",
            guard_companion_id="guard-1",
        )


async def test_reclaim_reuses_owner_device_binding_identity(store: DataStore) -> None:
    await store.owners.create(owner_id="owner-1", display_name="Owner")
    await store.devices.create_device(
        device_id="atk-1",
        owner_id=None,
        capabilities_json={"guard": {"enabled": True, "protocol_versions": [1]}},
    )
    first = await store.guard_bindings.claim(
        owner_id="owner-1",
        device_id="atk-1",
        guard_companion_id="guard-1",
    )
    await store.guard_bindings.disable(first.binding_id)

    reclaimed = await store.guard_bindings.claim(
        owner_id="owner-1",
        device_id="atk-1",
        guard_companion_id="guard-1",
    )

    assert reclaimed.binding_id == first.binding_id
    assert reclaimed.state == "active"
    assert reclaimed.config_revision == 2
    assert reclaimed.runtime_revision == 3
    assert reclaimed.disabled_at is None
    assert len(await store.guard_bindings.list_for_owner("owner-1")) == 1
    deliveries = await store.guard_runtime_deliveries.list_for_binding(first.binding_id)
    assert [
        (row.runtime_revision, row.desired_runtime_state)
        for row in deliveries
    ] == [
        (1, "running"),
        (2, "stopped"),
        (3, "running"),
    ]


async def test_guard_policy_config_is_normalized_and_revision_checked(store: DataStore) -> None:
    await store.owners.create(owner_id="owner-1", display_name="Owner")
    await store.devices.create_device(
        device_id="atk-1",
        owner_id=None,
        capabilities_json={"guard": {"enabled": True, "protocol_versions": [1]}},
    )
    binding = await store.guard_bindings.claim(
        owner_id="owner-1",
        device_id="atk-1",
        guard_companion_id="guard-1",
        config_json={"candidate_enabled": False},
    )
    assert binding.config_revision == 1
    assert binding.config_json == {
        "schema_v": 1,
        "candidate_enabled": False,
        "verified_enabled": True,
        "accepted_verdicts": ["present", "unknown"],
        "absence_enabled": True,
    }

    updated = await store.guard_bindings.update_config(
        binding_id=binding.binding_id,
        expected_revision=1,
        config_json={"absence_enabled": False},
    )
    assert updated.config_revision == 2
    assert updated.config_json["absence_enabled"] is False

    runtime_updated = await store.guard_bindings.update_runtime_config(
        binding_id=binding.binding_id,
        expected_revision=1,
        runtime_config_json={
            "sample_interval_ms": 600,
            "preview_interval_ms": 600,
            "motion_threshold": 20,
            "motion_clear_threshold": 10,
            "candidate_debounce_ms": 600,
            "absence_timeout_ms": 1200,
            "consecutive_capture_failures": 3,
        },
    )
    assert runtime_updated.runtime_revision == 2
    assert runtime_updated.runtime_config_json["sample_interval_ms"] == 600
    deliveries = await store.guard_runtime_deliveries.list_for_binding(binding.binding_id)
    assert [(row.runtime_revision, row.desired_runtime_state) for row in deliveries] == [
        (1, "running"),
        (2, "running"),
    ]

    with pytest.raises(ValueError, match="runtime configuration changed"):
        await store.guard_bindings.update_runtime_config(
            binding_id=binding.binding_id,
            expected_revision=1,
            runtime_config_json={},
        )

    with pytest.raises(ValueError, match="configuration changed"):
        await store.guard_bindings.update_config(
            binding_id=binding.binding_id,
            expected_revision=1,
            config_json={},
        )

    with pytest.raises(ValueError, match="extra_forbidden"):
        await store.guard_bindings.update_config(
            binding_id=binding.binding_id,
            expected_revision=2,
            config_json={"raw_audio": "forbidden"},
        )


async def test_guard_policy_action_outbox_survives_acknowledgement(store: DataStore) -> None:
    await store.owners.create(owner_id="owner-1", display_name="Owner")
    await store.devices.create_device(
        device_id="atk-1",
        owner_id=None,
        capabilities_json={"guard": {"enabled": True, "protocol_versions": [1]}},
    )
    binding = await store.guard_bindings.claim(
        owner_id="owner-1",
        device_id="atk-1",
        guard_companion_id="guard-1",
    )
    action = await store.guard_actions.publish(
        action_id="guard-action-1",
        binding_id=binding.binding_id,
        owner_id="owner-1",
        guard_companion_id="guard-1",
        device_id="atk-1",
        correlation_id="corr-1",
        guard_epoch=1,
        policy_id="silent_presence",
        action="mission_control.annotate",
        subscriber="mission_control_fixture",
        payload_json={"presence": "candidate"},
    )
    assert [row.action_id for row in await store.guard_actions.list_pending()] == [action.action_id]
    assert [
        row.action_id
        for row in await store.guard_actions.list_pending(subscriber="mission_control_fixture")
    ] == [action.action_id]
    updated = await store.guard_actions.acknowledge(
        action.action_id,
        status="completed",
        ack_json={"status": "completed"},
    )
    assert updated.status == "acknowledged"
    assert await store.guard_actions.list_pending() == []

    repeated = await store.guard_actions.acknowledge(
        action.action_id,
        status="completed",
        ack_json={"status": "completed", "repeat": True},
    )
    assert repeated.status == "acknowledged"
    assert repeated.ack_json == {"status": "completed"}
    with pytest.raises(ValueError, match="terminal acknowledgement"):
        await store.guard_actions.acknowledge(
            action.action_id,
            status="failed",
            ack_json={"status": "failed"},
        )


async def test_guard_policy_action_tracks_fact_replay_and_command_mapping(store: DataStore) -> None:
    await store.owners.create(owner_id="owner-1", display_name="Owner")
    await store.devices.create_device(
        device_id="atk-1",
        owner_id=None,
        capabilities_json={"guard": {"enabled": True, "protocol_versions": [1]}},
    )
    binding = await store.guard_bindings.claim(
        owner_id="owner-1",
        device_id="atk-1",
        guard_companion_id="guard-1",
    )
    await store.guard_actions.publish(
        action_id="guard-action-body-1",
        binding_id=binding.binding_id,
        owner_id="owner-1",
        guard_companion_id="guard-1",
        device_id="atk-1",
        correlation_id="corr-1",
        guard_epoch=7,
        fact_type="guard.presence.candidate",
        policy_id="silent_presence",
        action="body.presence.set",
        subscriber="stackchan-1",
        payload_json={"state": "awake"},
    )

    replayed = await store.guard_actions.list_for_fact(
        binding_id=binding.binding_id,
        correlation_id="corr-1",
        guard_epoch=7,
        fact_type="guard.presence.candidate",
    )
    assert [row.action_id for row in replayed] == ["guard-action-body-1"]

    claimed = await store.guard_actions.claim_for_dispatch("guard-action-body-1")
    assert claimed is not None and claimed.delivery_claim_token
    mapped = await store.guard_actions.mark_dispatched(
        "guard-action-body-1",
        claim_token=claimed.delivery_claim_token,
        command_id="cmd-body-1",
    )
    assert mapped is not None and mapped.command_id == "cmd-body-1"
    by_command = await store.guard_actions.get_by_command_id("cmd-body-1")
    assert by_command is not None and by_command.action_id == "guard-action-body-1"
