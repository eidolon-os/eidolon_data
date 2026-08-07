from __future__ import annotations

import pytest

from eidolon_data.services.owner_workspace import OwnerWorkspaceError

pytestmark = pytest.mark.component


async def test_owner_commands_create_update_archive_and_audit(store) -> None:
    created = await store.owner_commands.create_owner(
        owner_id="owner-1",
        display_name="Original",
        profile_json={"locale": "zh-CN"},
    )
    assert created.owner.status == "active"
    updated = await store.owner_commands.update_owner(
        owner_id="owner-1",
        display_name="Updated",
        settings_json={"quiet_hours": True},
    )
    assert updated.display_name == "Updated"
    assert updated.settings_json == {"quiet_hours": True}
    archived = await store.owner_commands.archive_owner("owner-1")
    assert archived.status == "archived"
    assert [event.action for event in await store.audit_outbox.list_pending()] == [
        "owner.created",
        "owner.updated",
        "owner.archived",
    ]


@pytest.mark.parametrize("owner_id", ["", " bad", "bad:id", "x" * 49])
async def test_owner_id_validation_is_fail_closed(store, owner_id: str) -> None:
    with pytest.raises(OwnerWorkspaceError, match="owner_id"):
        await store.owner_commands.create_owner(owner_id=owner_id)


async def test_owner_duplicate_invalid_kind_and_inactive_update_are_rejected(store) -> None:
    await store.owner_commands.create_owner(owner_id="owner-1")
    with pytest.raises(OwnerWorkspaceError, match="already exists"):
        await store.owner_commands.create_owner(owner_id="owner-1")
    with pytest.raises(OwnerWorkspaceError, match="kind"):
        await store.owner_commands.create_owner(owner_id="owner-2", kind="device")
    await store.owner_commands.archive_owner("owner-1")
    with pytest.raises(OwnerWorkspaceError, match="active"):
        await store.owner_commands.update_owner(owner_id="owner-1", display_name="No")
    with pytest.raises(KeyError, match="not found"):
        await store.owner_commands.archive_owner("missing")


async def test_workspace_is_one_atomic_identity_persona_memory_transaction(store) -> None:
    await store.owner_commands.create_owner(owner_id="owner-workspace", display_name="Owner")
    result = await store.companion_workspaces.provision_workspace(
        owner_id="owner-workspace",
        companion_id="companion-1",
        companion_display_name="Eidolon",
        genome_id="genome-1",
        realm_id="realm-1",
        role="primary",
        companion_runtime_config_json={"voice": "warm"},
    )
    assert result.companion.role == "primary"
    assert result.companion.current_genome_id == "genome-1"
    assert result.companion.default_memory_realm_id == "realm-1"
    assert result.persona_genome.status == "committed"
    assert result.persona_genome.applied_event_id is not None
    assert result.memory_realm.engine == "mempalace"
    assert (await store.persona_genomes.get_current("companion-1")).genome_id == "genome-1"
    assert (await store.memory_realms.get("realm-1")).companion_id == "companion-1"
    assert [event.action for event in await store.audit_outbox.list_pending()] == [
        "owner.created",
        "companion.workspace.initialized",
    ]


async def test_owner_workspace_onboarding_is_atomic_durable_and_idempotent(store) -> None:
    operation_id = "f8b886ff-d2e5-4d73-bb10-53d4a43f319e"
    fingerprint = "sha256:" + "a" * 64
    first = await store.companion_workspaces.initialize_owner_workspace(
        operation_id=operation_id,
        request_fingerprint=fingerprint,
        owner_display_name="Manson",
        companion_display_name="Eidolon",
    )
    assert first.owner.owner_id == "owner_f8b886ffd2e54d73bb1053d4a43f319e"
    assert first.workspace.companion.companion_id == "c_f8b886ffd2e54d73bb1053d4a43f319e"
    assert first.workspace.companion.role == "primary"
    assert first.workspace.companion.metadata_json["_eidolon_onboarding"] == {
        "operation_id": operation_id,
        "request_fingerprint": fingerprint,
    }

    replay = await store.companion_workspaces.initialize_owner_workspace(
        operation_id=operation_id,
        request_fingerprint=fingerprint,
        owner_display_name="Manson",
        companion_display_name="Eidolon",
    )
    queried = await store.companion_workspaces.get_owner_workspace_initialization(operation_id)
    assert replay.owner.owner_id == first.owner.owner_id
    assert replay.workspace.persona_genome.genome_id == first.workspace.persona_genome.genome_id
    assert queried is not None
    assert queried.request_fingerprint == fingerprint
    assert len(await store.owners.list()) == 1
    assert len(await store.companions.list_for_owner(first.owner.owner_id)) == 1
    assert [event.trace_id for event in await store.audit_outbox.list_pending()] == [
        operation_id,
        operation_id,
    ]

    with pytest.raises(OwnerWorkspaceError, match="already in use"):
        await store.companion_workspaces.initialize_owner_workspace(
            operation_id=operation_id,
            request_fingerprint="sha256:" + "b" * 64,
            owner_display_name="Another Owner",
            companion_display_name="Another Companion",
        )


async def test_owner_workspace_onboarding_rolls_back_owner_on_workspace_collision(store) -> None:
    operation_id = "731de841-bc18-422c-9a92-34c4ebcb8bd8"
    operation_hex = "731de841bc18422c9a9234c4ebcb8bd8"
    await store.owner_commands.create_owner(owner_id="existing-owner")
    await store.companion_workspaces.provision_workspace(
        owner_id="existing-owner",
        companion_id="existing-companion",
        genome_id="existing-genome",
        realm_id=f"r_{operation_hex}",
    )

    with pytest.raises(OwnerWorkspaceError, match="identifier already exists"):
        await store.companion_workspaces.initialize_owner_workspace(
            operation_id=operation_id,
            request_fingerprint="sha256:" + "c" * 64,
            owner_display_name="Interrupted Owner",
            companion_display_name="Eidolon",
        )

    assert await store.owners.get(f"owner_{operation_hex}") is None
    assert await store.companions.get(f"c_{operation_hex}") is None


async def test_workspace_collision_rolls_back_without_partial_companion(store) -> None:
    await store.owner_commands.create_owner(owner_id="owner-1")
    await store.companion_workspaces.provision_workspace(
        owner_id="owner-1",
        companion_id="existing",
        genome_id="shared-genome",
        realm_id="existing-realm",
    )
    with pytest.raises(OwnerWorkspaceError, match="identifier already exists"):
        await store.companion_workspaces.provision_workspace(
            owner_id="owner-1",
            companion_id="must-not-persist",
            genome_id="shared-genome",
            realm_id="new-realm",
        )
    assert await store.companions.get("must-not-persist") is None
    assert await store.memory_realms.get("new-realm") is None


async def test_workspace_rejects_unknown_role_and_inactive_owner(store) -> None:
    await store.owner_commands.create_owner(owner_id="owner-1")
    with pytest.raises(OwnerWorkspaceError, match="role"):
        await store.companion_workspaces.provision_workspace(owner_id="owner-1", role="master")
    await store.owner_commands.archive_owner("owner-1")
    with pytest.raises(OwnerWorkspaceError, match="not active"):
        await store.companion_workspaces.provision_workspace(owner_id="owner-1")


async def test_primary_role_is_unique_and_promotion_is_atomic(store) -> None:
    await store.owner_commands.create_owner(owner_id="owner-1")
    first = await store.companion_workspaces.provision_workspace(
        owner_id="owner-1",
        companion_id="companion-a",
        genome_id="genome-a",
        realm_id="realm-a",
        role="primary",
    )
    await store.companion_workspaces.provision_workspace(
        owner_id="owner-1",
        companion_id="companion-b",
        genome_id="genome-b",
        realm_id="realm-b",
    )
    with pytest.raises(OwnerWorkspaceError, match="already has"):
        await store.companion_workspaces.provision_workspace(
            owner_id="owner-1",
            companion_id="companion-c",
            genome_id="genome-c",
            realm_id="realm-c",
            role="primary",
        )
    promoted = await store.companion_workspaces.promote_to_primary(
        owner_id="owner-1", companion_id="companion-b"
    )
    assert promoted.role == "primary"
    assert (await store.companions.get(first.companion.companion_id)).role == "standard"
    assert (await store.companions.get_primary_for_owner("owner-1")).companion_id == "companion-b"


async def test_guard_cannot_be_promoted_and_realm_ensure_is_idempotent(store) -> None:
    await store.owner_commands.create_owner(owner_id="owner-1")
    guard = await store.companion_workspaces.provision_workspace(
        owner_id="owner-1",
        companion_id="guard-1",
        genome_id="genome-guard",
        realm_id="realm-guard",
        role="guard",
    )
    with pytest.raises(OwnerWorkspaceError, match="guard"):
        await store.companion_workspaces.promote_to_primary(
            owner_id="owner-1", companion_id="guard-1"
        )
    same = await store.companion_workspaces.ensure_memory_realm(
        owner_id="owner-1", companion_id="guard-1"
    )
    assert same.realm_id == guard.memory_realm.realm_id
    assert len(await store.memory_realms.list_for_owner("owner-1")) == 1


async def test_archiving_owner_inactivates_all_active_companions(store) -> None:
    await store.owner_commands.create_owner(owner_id="owner-1")
    for suffix in ("a", "b", "guard"):
        await store.companion_workspaces.provision_workspace(
            owner_id="owner-1",
            companion_id=f"companion-{suffix}",
            genome_id=f"genome-{suffix}",
            realm_id=f"realm-{suffix}",
            role=("primary" if suffix == "a" else "guard" if suffix == "guard" else "standard"),
        )
    binding = await store.guard_bindings.bind(
        owner_id="owner-1",
        guard_companion_id="companion-guard",
        device_id="external-device",
    )
    await store.owner_commands.archive_owner("owner-1")
    assert {row.status for row in await store.companions.list_for_owner("owner-1")} == {"inactive"}
    assert await store.companions.get_primary_for_owner("owner-1") is None
    assert {row.status for row in await store.memory_realms.list_for_owner("owner-1")} == {
        "inactive"
    }
    assert (await store.guard_bindings.get(binding.binding_id)).state == "disabled"
