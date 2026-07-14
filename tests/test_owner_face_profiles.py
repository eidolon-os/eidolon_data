from __future__ import annotations

import hashlib

import pytest
from sqlalchemy.exc import IntegrityError

from eidolon_data import DataSettings, DataStore
from eidolon_data.repositories.owner_face_profiles import _is_profile_revision_conflict


@pytest.fixture
async def store(tmp_path):
    value = DataStore.open(
        DataSettings(
            sqlite_path=str(tmp_path / "owner-face.sqlite3"),
            object_store_path=str(tmp_path / "objects"),
        )
    )
    await value.init_schema()
    try:
        yield value
    finally:
        await value.close()


async def _create_binding(store: DataStore, *, owner_id: str, device_id: str):
    await store.owners.create(owner_id=owner_id, display_name=owner_id)
    await store.devices.create_device(
        device_id=device_id,
        owner_id=None,
        capabilities_json={"guard": {"enabled": True, "protocol_versions": [1]}},
    )
    return await store.guard_bindings.claim(
        owner_id=owner_id,
        device_id=device_id,
        guard_companion_id=f"guard-{owner_id}",
    )


async def _add_reference(store: DataStore, profile_revision_id: str, owner_id: str, pose: str):
    content = f"normalized-{owner_id}-{pose}".encode()
    digest = hashlib.sha256(content).hexdigest()
    storage_key = f"{owner_id}/owner-face/{profile_revision_id}/{pose}.jpg"
    store.object_storage.put(storage_key, content, expected_sha256=digest)
    return await store.owner_face_profiles.add_reference(
        profile_revision_id=profile_revision_id,
        pose=pose,
        content_type="image/jpeg",
        size_bytes=len(content),
        sha256=digest,
        storage_key=storage_key,
    )


def test_profile_integrity_error_classification_is_narrow() -> None:
    schema_drift = IntegrityError(
        "INSERT",
        {},
        RuntimeError(
            "NOT NULL constraint failed: owner_face_profile_revisions.created_by"
        ),
    )
    revision_race = IntegrityError(
        "INSERT",
        {},
        RuntimeError(
            "UNIQUE constraint failed: owner_face_profile_revisions.owner_id, "
            "owner_face_profile_revisions.revision"
        ),
    )

    assert not _is_profile_revision_conflict(schema_drift)
    assert _is_profile_revision_conflict(revision_race)


async def test_profile_activation_fans_out_and_result_is_strict(store: DataStore) -> None:
    binding = await _create_binding(store, owner_id="owner-1", device_id="atk-1")
    profile = await store.owner_face_profiles.create_draft(
        owner_id="owner-1",
        model_id="esp-who-human-face-recognition-v1",
        preprocessing_version="rgb565-be-qvga-v1",
    )
    references = [
        await _add_reference(store, profile.profile_revision_id, "owner-1", pose)
        for pose in ("front", "left", "right", "down")
    ]

    activated = await store.owner_face_profiles.activate(profile.profile_revision_id)
    assert activated.state == "desired"
    deliveries = await store.guard_owner_face_profile_deliveries.list_for_binding(
        binding.binding_id
    )
    assert [(row.profile_revision, row.desired_state, row.status) for row in deliveries] == [
        (1, "active", "pending")
    ]

    claimed = await store.guard_owner_face_profile_deliveries.claim_for_dispatch(
        deliveries[0].delivery_id
    )
    assert claimed is not None and claimed.attempt_count == 1
    dispatched = await store.guard_owner_face_profile_deliveries.mark_dispatched(
        claimed.delivery_id, command_id="cmd-owner-face-1"
    )
    assert dispatched is not None
    applied = await store.guard_owner_face_profile_deliveries.record_command_result(
        "cmd-owner-face-1",
        status="succeeded",
        result_json={
            "binding_id": binding.binding_id,
            "profile_id": activated.profile_id,
            "profile_revision": 1,
            "applied_state": "active",
            "model_id": "esp-who-human-face-recognition-v1",
            "preprocessing_version": "rgb565-be-qvga-v1",
            "template_count": 4,
        },
    )
    assert applied is not None and applied.status == "applied"

    resolved = await store.owner_face_profiles.get_reference_for_active_device(
        device_id="atk-1", reference_id=references[0].reference_id
    )
    assert resolved is not None
    assert store.object_storage.get(resolved.storage_key).startswith(b"normalized-")


async def test_transient_command_result_has_bounded_retry_budget(store: DataStore) -> None:
    binding = await _create_binding(store, owner_id="owner-retry", device_id="atk-retry")
    profile = await store.owner_face_profiles.create_draft(
        owner_id="owner-retry",
        model_id="esp-who-human-face-recognition-v1",
        preprocessing_version="rgb565-be-qvga-v1",
    )
    for pose in ("front", "left", "right"):
        await _add_reference(store, profile.profile_revision_id, "owner-retry", pose)
    await store.owner_face_profiles.activate(profile.profile_revision_id)
    delivery = (
        await store.guard_owner_face_profile_deliveries.list_for_binding(binding.binding_id)
    )[0]

    for attempt in range(1, 6):
        claimed = await store.guard_owner_face_profile_deliveries.claim_for_dispatch(
            delivery.delivery_id
        )
        assert claimed is not None and claimed.attempt_count == attempt
        command_id = f"cmd-retry-{attempt}"
        await store.guard_owner_face_profile_deliveries.mark_dispatched(
            delivery.delivery_id, command_id=command_id
        )
        retried = await store.guard_owner_face_profile_deliveries.retry_command_result(
            command_id,
            error="OWNER_FACE_REFERENCE_FETCH_FAILED",
            max_attempts=5,
        )
        assert retried is not None
        assert retried.status == ("pending" if attempt < 5 else "failed")
        assert retried.command_id == (None if attempt < 5 else command_id)


async def test_profile_requires_three_canonical_poses_and_enforces_owner_scope(
    store: DataStore,
) -> None:
    await _create_binding(store, owner_id="owner-1", device_id="atk-1")
    await _create_binding(store, owner_id="owner-2", device_id="atk-2")
    profile = await store.owner_face_profiles.create_draft(
        owner_id="owner-1",
        model_id="model-v1",
        preprocessing_version="pre-v1",
    )
    front = await _add_reference(store, profile.profile_revision_id, "owner-1", "front")
    await _add_reference(store, profile.profile_revision_id, "owner-1", "left")

    with pytest.raises(ValueError, match="three to five"):
        await store.owner_face_profiles.activate(profile.profile_revision_id)

    await _add_reference(store, profile.profile_revision_id, "owner-1", "down")
    with pytest.raises(ValueError, match="front, left, and right"):
        await store.owner_face_profiles.activate(profile.profile_revision_id)

    await _add_reference(store, profile.profile_revision_id, "owner-1", "right")
    await store.owner_face_profiles.activate(profile.profile_revision_id)
    assert (
        await store.owner_face_profiles.get_reference_for_active_device(
            device_id="atk-2", reference_id=front.reference_id
        )
        is None
    )


async def test_replacement_and_clear_supersede_pending_delivery(store: DataStore) -> None:
    binding = await _create_binding(store, owner_id="owner-1", device_id="atk-1")
    first = await store.owner_face_profiles.create_draft(
        owner_id="owner-1",
        model_id="model-v1",
        preprocessing_version="pre-v1",
    )
    for pose in ("front", "left", "right"):
        await _add_reference(store, first.profile_revision_id, "owner-1", pose)
    await store.owner_face_profiles.activate(first.profile_revision_id)

    cleared = await store.owner_face_profiles.clear(owner_id="owner-1")
    assert cleared.revision == 2 and cleared.desired_state == "cleared"
    superseded = await store.owner_face_profiles.list_superseded_references("owner-1")
    assert len(superseded) == 3
    deleted = await store.owner_face_profiles.delete_superseded_references(
        owner_id="owner-1",
        reference_ids=[reference.reference_id for reference in superseded],
    )
    assert deleted == 3
    assert await store.owner_face_profiles.list_references(first.profile_revision_id) == []
    deliveries = await store.guard_owner_face_profile_deliveries.list_for_binding(
        binding.binding_id
    )
    assert [(row.profile_revision, row.desired_state, row.status) for row in deliveries] == [
        (1, "active", "superseded"),
        (2, "cleared", "pending"),
    ]


async def test_existing_desired_profile_is_enqueued_when_device_is_claimed(store: DataStore) -> None:
    await store.owners.create(owner_id="owner-1", display_name="Owner")
    profile = await store.owner_face_profiles.create_draft(
        owner_id="owner-1",
        model_id="model-v1",
        preprocessing_version="pre-v1",
    )
    for pose in ("front", "left", "right"):
        await _add_reference(store, profile.profile_revision_id, "owner-1", pose)
    await store.owner_face_profiles.activate(profile.profile_revision_id)
    await store.devices.create_device(
        device_id="atk-1",
        owner_id=None,
        capabilities_json={"guard": {"enabled": True, "protocol_versions": [1]}},
    )

    binding = await store.guard_bindings.claim(
        owner_id="owner-1", device_id="atk-1", guard_companion_id="guard-1"
    )
    deliveries = await store.guard_owner_face_profile_deliveries.list_for_binding(
        binding.binding_id
    )
    assert [(row.profile_revision, row.status) for row in deliveries] == [(1, "pending")]
