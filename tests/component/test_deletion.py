from __future__ import annotations

import hashlib

import pytest

from eidolon_data.services.companion import CompanionDeletionError

pytestmark = pytest.mark.component


async def _workspace(store, owner_id: str, companion_id: str, *, role: str = "standard"):
    if await store.owners.get(owner_id) is None:
        await store.owner_commands.create_owner(owner_id=owner_id)
    return await store.companion_workspaces.provision_workspace(
        owner_id=owner_id,
        companion_id=companion_id,
        genome_id=f"genome-{companion_id}",
        realm_id=f"realm-{companion_id}",
        role=role,
    )


async def _face(store, companion_id: str, key: str):
    return await store.companion_faces.set_face(
        companion_id=companion_id,
        cond_storage_key=key,
        cond_content_type="image/jpeg",
        cond_size_bytes=3,
        cond_sha256=hashlib.sha256(b"jpg").hexdigest(),
    )


async def test_primary_companion_delete_requires_explicit_override(store) -> None:
    await _workspace(store, "owner-1", "companion-1", role="primary")
    with pytest.raises(CompanionDeletionError, match="primary"):
        await store.companion_deletion.delete_companion(
            owner_id="owner-1", companion_id="companion-1"
        )
    assert await store.companions.get("companion-1") is not None


async def test_companion_delete_cascades_owned_rows_and_returns_external_keys(store) -> None:
    workspace = await _workspace(store, "owner-1", "guard-1", role="guard")
    face = await _face(store, "guard-1", "owner-1/faces/guard.jpg")
    await store.guard_bindings.bind(
        owner_id="owner-1", guard_companion_id="guard-1", device_id="external-device"
    )
    result = await store.companion_deletion.delete_companion(
        owner_id="owner-1", companion_id="guard-1"
    )
    assert result.realm_ids == (workspace.memory_realm.realm_id,)
    assert result.face_asset_storage_keys == (face.cond_storage_key,)
    assert result.deleted_rows == {
        "companions": 1,
        "persona_genomes": 1,
        "memory_realms": 1,
        "companion_face_assets": 1,
        "guard_bindings": 1,
    }
    assert await store.companions.get("guard-1") is None
    assert await store.persona_genomes.get(workspace.persona_genome.genome_id) is None
    assert await store.memory_realms.get(workspace.memory_realm.realm_id) is None
    assert await store.guard_bindings.list_for_owner("owner-1") == []
    assert (await store.audit_outbox.list_pending())[-1].action == "companion.deleted"


async def test_companion_delete_is_owner_scoped(store) -> None:
    await _workspace(store, "owner-a", "companion-a")
    with pytest.raises(CompanionDeletionError, match="not found"):
        await store.companion_deletion.delete_companion(
            owner_id="owner-b", companion_id="companion-a"
        )


async def test_owner_delete_cascades_only_target_and_reports_cleanup(store) -> None:
    await _workspace(store, "owner-delete", "companion-delete")
    await _workspace(store, "owner-keep", "companion-keep")
    face = await _face(store, "companion-delete", "owner-delete/companion-faces/current.jpg")
    draft = await store.owner_faces.create_draft(
        owner_id="owner-delete", model_id="model-v1", preprocessing_version="pre-v1"
    )
    reference_key = "owner-delete/owner-face/front.jpg"
    await store.owner_faces.add_reference(
        profile_revision_id=draft.profile_revision_id,
        pose="front",
        content_type="image/jpeg",
        size_bytes=3,
        sha256=hashlib.sha256(b"ref").hexdigest(),
        storage_key=reference_key,
    )
    result = await store.owner_deletion.delete_owner("owner-delete")
    assert result.deleted is True
    assert set(result.object_storage_keys) == {face.cond_storage_key, reference_key}
    assert result.realm_ids == ("realm-companion-delete",)
    assert await store.owners.get("owner-delete") is None
    assert await store.owners.get("owner-keep") is not None
    assert await store.companions.get("companion-keep") is not None
    assert (await store.audit_outbox.list_pending())[-1].action == "owner.deleted"


async def test_delete_missing_owner_is_idempotent(store) -> None:
    result = await store.owner_deletion.delete_owner("missing")
    assert result.deleted is False
    assert result.deleted_rows == {}
