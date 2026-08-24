from __future__ import annotations

import pytest

pytestmark = pytest.mark.component


async def _guard(store, *, owner_id: str = "owner-1", companion_id: str = "guard-1"):
    if await store.owners.get(owner_id) is None:
        await store.owner_commands.create_owner(owner_id=owner_id)
    return await store.companion_workspaces.provision_workspace(
        owner_id=owner_id,
        companion_id=companion_id,
        genome_id=f"genome-{companion_id}",
        realm_id=f"realm-{companion_id}",
        kind="guard",
    )


async def test_bind_uses_opaque_external_device_reference_and_audits(store) -> None:
    await _guard(store)
    row = await store.guard_bindings.bind(
        owner_id="owner-1",
        guard_companion_id="guard-1",
        device_id="hub:device:atk-1",
        policy_id="silent_presence",
        policy_json={"distance_cm": 200},
    )
    assert row.state == "active"
    assert row.policy_revision == 1
    assert (
        await store.guard_bindings.get_active_for_device("hub:device:atk-1")
    ).binding_id == row.binding_id
    facts = await store.audit_outbox.list_pending()
    assert facts[-1].action == "guard.binding.created"
    assert facts[-1].payload["device_id"] == "hub:device:atk-1"


async def test_binding_requires_same_owner_active_guard_companion(store) -> None:
    await _guard(store, owner_id="owner-a", companion_id="guard-a")
    await _guard(store, owner_id="owner-b", companion_id="guard-b")
    standard = await store.companion_workspaces.provision_workspace(
        owner_id="owner-a",
        companion_id="standard-a",
        genome_id="genome-standard",
        realm_id="realm-standard",
        kind="conversational",
    )
    with pytest.raises(ValueError, match="not found for owner"):
        await store.guard_bindings.bind(
            owner_id="owner-a", guard_companion_id="guard-b", device_id="device-1"
        )
    with pytest.raises(ValueError, match="kind must be guard"):
        await store.guard_bindings.bind(
            owner_id="owner-a",
            guard_companion_id=standard.companion.companion_id,
            device_id="device-1",
        )


@pytest.mark.parametrize("field,value", [("device_id", "bad/id"), ("policy_id", "bad policy")])
async def test_binding_rejects_unsafe_opaque_identifiers(store, field: str, value: str) -> None:
    await _guard(store)
    kwargs = {
        "owner_id": "owner-1",
        "guard_companion_id": "guard-1",
        "device_id": "device-1",
        "policy_id": "policy-1",
    }
    kwargs[field] = value
    with pytest.raises(ValueError, match=field):
        await store.guard_bindings.bind(**kwargs)


async def test_active_binding_uniqueness_is_released_by_disable(store) -> None:
    await _guard(store, companion_id="guard-a")
    await _guard(store, companion_id="guard-b")
    first = await store.guard_bindings.bind(
        owner_id="owner-1", guard_companion_id="guard-a", device_id="device-1"
    )
    with pytest.raises(ValueError, match="already exists"):
        await store.guard_bindings.bind(
            owner_id="owner-1", guard_companion_id="guard-b", device_id="device-1"
        )
    disabled = await store.guard_bindings.disable(first.binding_id)
    assert disabled.state == "disabled"
    replacement = await store.guard_bindings.bind(
        owner_id="owner-1", guard_companion_id="guard-b", device_id="device-1"
    )
    assert replacement.state == "active"


async def test_policy_compare_and_swap_and_terminal_transitions(store) -> None:
    await _guard(store)
    binding = await store.guard_bindings.bind(
        owner_id="owner-1", guard_companion_id="guard-1", device_id="device-1"
    )
    updated = await store.guard_bindings.update_policy(
        binding_id=binding.binding_id,
        expected_revision=1,
        policy_id="presence_alert",
        policy_json={"threshold": 0.8},
    )
    assert updated.policy_revision == 2
    with pytest.raises(ValueError, match="revision conflict"):
        await store.guard_bindings.update_policy(
            binding_id=binding.binding_id,
            expected_revision=1,
            policy_id="presence_alert",
            policy_json={},
        )
    revoked = await store.guard_bindings.disable(binding.binding_id, revoke=True)
    assert revoked.state == "revoked" and revoked.revoked_at is not None
    same = await store.guard_bindings.disable(binding.binding_id, revoke=True)
    assert same.state == "revoked"
    with pytest.raises(ValueError, match="only an active"):
        await store.guard_bindings.update_policy(
            binding_id=binding.binding_id,
            expected_revision=2,
            policy_id="other",
            policy_json={},
        )


async def test_list_for_owner_is_strictly_scoped(store) -> None:
    await _guard(store, owner_id="owner-a", companion_id="guard-a")
    await _guard(store, owner_id="owner-b", companion_id="guard-b")
    await store.guard_bindings.bind(
        owner_id="owner-a", guard_companion_id="guard-a", device_id="device-a"
    )
    await store.guard_bindings.bind(
        owner_id="owner-b", guard_companion_id="guard-b", device_id="device-b"
    )
    assert [row.device_id for row in await store.guard_bindings.list_for_owner("owner-a")] == [
        "device-a"
    ]


async def test_missing_binding_and_invalid_expected_revision_fail_closed(store) -> None:
    with pytest.raises(KeyError, match="not found"):
        await store.guard_bindings.disable("missing")
    with pytest.raises(ValueError, match="positive"):
        await store.guard_bindings.update_policy(
            binding_id="missing",
            expected_revision=0,
            policy_id="policy",
            policy_json={},
        )
