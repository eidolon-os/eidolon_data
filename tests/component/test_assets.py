from __future__ import annotations

import hashlib

import pytest

pytestmark = pytest.mark.component


async def _companion(store):
    await store.owner_commands.create_owner(owner_id="owner-1")
    return await store.companion_workspaces.provision_workspace(
        owner_id="owner-1",
        companion_id="companion-1",
        genome_id="genome-1",
        realm_id="realm-1",
    )


async def _set_face(store, marker: bytes):
    content = b"\xff\xd8\xff" + marker
    digest = hashlib.sha256(content).hexdigest()
    key = f"owner-1/companion-faces/{digest}.jpg"
    store.object_storage.put(key, content, expected_sha256=digest)
    row = await store.companion_faces.set_face(
        companion_id="companion-1",
        cond_storage_key=key,
        cond_content_type="image/jpeg",
        cond_size_bytes=len(content),
        cond_sha256=digest,
        width=448,
        height=448,
    )
    return row, content


async def _add_owner_reference(store, profile_revision_id: str, pose: str):
    content = f"normalized-{pose}".encode()
    digest = hashlib.sha256(content).hexdigest()
    key = f"owner-1/owner-face/{profile_revision_id}/{pose}.jpg"
    store.object_storage.put(key, content, expected_sha256=digest)
    return await store.owner_faces.add_reference(
        profile_revision_id=profile_revision_id,
        pose=pose,
        content_type="image/jpeg",
        size_bytes=len(content),
        sha256=digest,
        storage_key=key,
    )


async def test_companion_face_versions_supersede_and_clear(store) -> None:
    await _companion(store)
    v1, _ = await _set_face(store, b"one")
    v2, content = await _set_face(store, b"two")
    assert [
        (row.version, row.state)
        for row in await store.companion_faces.list_for_companion("companion-1")
    ] == [
        (2, "active"),
        (1, "superseded"),
    ]
    assert store.object_storage.get(v2.cond_storage_key) == content
    assert await store.companion_faces.clear("companion-1") is True
    assert await store.companion_faces.clear("companion-1") is False
    assert await store.companion_faces.get_active("companion-1") is None
    assert {v1.cond_storage_key, v2.cond_storage_key} == set(
        await store.companion_faces.list_storage_keys_for_companion("companion-1")
    )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"cond_content_type": "image/png"}, "image/jpeg"),
        ({"cond_size_bytes": 0}, "positive"),
        ({"cond_sha256": "BAD"}, "hexadecimal"),
        ({"cond_storage_key": "../bad"}, "safe relative"),
    ],
)
async def test_companion_face_validation(store, override: dict, message: str) -> None:
    await _companion(store)
    values = {
        "companion_id": "companion-1",
        "cond_storage_key": "owner-1/face.jpg",
        "cond_content_type": "image/jpeg",
        "cond_size_bytes": 10,
        "cond_sha256": "a" * 64,
    }
    with pytest.raises(ValueError, match=message):
        await store.companion_faces.set_face(**{**values, **override})


async def test_companion_idle_clip_lifecycle_and_integrity(store) -> None:
    await _companion(store)
    asset, _ = await _set_face(store, b"idle")
    await store.companion_faces.set_idle_status(asset.face_asset_id, "generating")
    idle = b"fake-fmp4"
    ready = await store.companion_faces.set_idle_clip(
        asset.face_asset_id,
        storage_key="owner-1/companion-faces/idle.mp4",
        content_type="video/mp4",
        size_bytes=len(idle),
        sha256=hashlib.sha256(idle).hexdigest(),
    )
    assert ready.idle_status == "ready"
    assert ready.idle_storage_key.endswith("idle.mp4")
    await store.companion_faces.set_idle_status(
        asset.face_asset_id, "failed", error="renderer unavailable"
    )
    failed = await store.companion_faces.get(asset.face_asset_id)
    assert failed.idle_error == "renderer unavailable"
    with pytest.raises(ValueError, match="unsupported"):
        await store.companion_faces.set_idle_status(asset.face_asset_id, "ready")


async def test_owner_face_requires_compatibility_and_canonical_poses(store) -> None:
    await store.owner_commands.create_owner(owner_id="owner-1")
    with pytest.raises(ValueError, match="compatibility"):
        await store.owner_faces.create_draft(
            owner_id="owner-1", model_id="", preprocessing_version="pre-v1"
        )
    draft = await store.owner_faces.create_draft(
        owner_id="owner-1", model_id="model-v1", preprocessing_version="pre-v1"
    )
    for pose in ("front", "left"):
        await _add_owner_reference(store, draft.profile_revision_id, pose)
    with pytest.raises(ValueError, match="three to five"):
        await store.owner_faces.activate(draft.profile_revision_id)
    await _add_owner_reference(store, draft.profile_revision_id, "down")
    with pytest.raises(ValueError, match="front, left, and right"):
        await store.owner_faces.activate(draft.profile_revision_id)
    await _add_owner_reference(store, draft.profile_revision_id, "right")
    desired = await store.owner_faces.activate(draft.profile_revision_id)
    assert desired.state == "desired"
    assert [
        row.pose for row in await store.owner_faces.list_references(draft.profile_revision_id)
    ] == [
        "front",
        "left",
        "right",
        "down",
    ]


async def test_owner_face_duplicate_pose_and_input_validation(store) -> None:
    await store.owner_commands.create_owner(owner_id="owner-1")
    draft = await store.owner_faces.create_draft(
        owner_id="owner-1", model_id="model-v1", preprocessing_version="pre-v1"
    )
    await _add_owner_reference(store, draft.profile_revision_id, "front")
    with pytest.raises(ValueError, match="already present"):
        await _add_owner_reference(store, draft.profile_revision_id, "front")
    with pytest.raises(ValueError, match="unsupported"):
        await store.owner_faces.add_reference(
            profile_revision_id=draft.profile_revision_id,
            pose="diagonal",
            content_type="image/jpeg",
            size_bytes=1,
            sha256="a" * 64,
            storage_key="owner-1/diagonal.jpg",
        )


@pytest.mark.parametrize(
    ("override", "message"),
    [
        ({"pose": "diagonal"}, "unsupported"),
        ({"content_type": "image/png"}, "image/jpeg"),
        ({"size_bytes": 0}, "positive"),
        ({"sha256": "BAD"}, "hexadecimal"),
        ({"storage_key": "../outside.jpg"}, "safe relative"),
    ],
)
async def test_owner_face_reference_validation_is_fail_closed(
    store, override: dict, message: str
) -> None:
    await store.owner_commands.create_owner(owner_id="owner-1")
    draft = await store.owner_faces.create_draft(
        owner_id="owner-1", model_id="model-v1", preprocessing_version="pre-v1"
    )
    values = {
        "profile_revision_id": draft.profile_revision_id,
        "pose": "front",
        "content_type": "image/jpeg",
        "size_bytes": 3,
        "sha256": "a" * 64,
        "storage_key": "owner-1/front.jpg",
    }
    with pytest.raises(ValueError, match=message):
        await store.owner_faces.add_reference(**{**values, **override})


async def test_owner_face_draft_replacement_and_reference_limit(store) -> None:
    await store.owner_commands.create_owner(owner_id="owner-1")
    first = await store.owner_faces.create_draft(
        owner_id="owner-1", model_id="model-v1", preprocessing_version="pre-v1"
    )
    second = await store.owner_faces.create_draft(
        owner_id="owner-1", model_id="model-v2", preprocessing_version="pre-v2"
    )
    assert (await store.owner_faces.get_revision(first.profile_revision_id)).state == "superseded"
    references = [
        await _add_owner_reference(store, second.profile_revision_id, pose)
        for pose in ("front", "left", "right", "down", "up")
    ]
    assert len(references) == 5
    with pytest.raises(ValueError, match="already has five"):
        await store.owner_faces.add_reference(
            profile_revision_id=second.profile_revision_id,
            pose="front",
            content_type="image/jpeg",
            size_bytes=3,
            sha256="b" * 64,
            storage_key="owner-1/extra.jpg",
        )
    await store.owner_faces.activate(second.profile_revision_id)
    with pytest.raises(ValueError, match="only be added to a draft"):
        await store.owner_faces.add_reference(
            profile_revision_id=second.profile_revision_id,
            pose="front",
            content_type="image/jpeg",
            size_bytes=3,
            sha256="c" * 64,
            storage_key="owner-1/after-active.jpg",
        )


async def test_owner_face_requires_existing_owner_and_revision(store) -> None:
    with pytest.raises(KeyError, match="owner not found"):
        await store.owner_faces.create_draft(
            owner_id="missing", model_id="model-v1", preprocessing_version="pre-v1"
        )
    with pytest.raises(KeyError, match="revision not found"):
        await store.owner_faces.activate("missing-revision")
    with pytest.raises(KeyError, match="owner not found"):
        await store.owner_faces.clear(owner_id="missing")


async def test_owner_face_clear_supersedes_and_gc_is_owner_scoped(store) -> None:
    await store.owner_commands.create_owner(owner_id="owner-1")
    draft = await store.owner_faces.create_draft(
        owner_id="owner-1", model_id="model-v1", preprocessing_version="pre-v1"
    )
    references = [
        await _add_owner_reference(store, draft.profile_revision_id, pose)
        for pose in ("front", "left", "right")
    ]
    await store.owner_faces.activate(draft.profile_revision_id)
    cleared = await store.owner_faces.clear(owner_id="owner-1")
    assert cleared.revision == 2
    assert cleared.desired_state == "cleared"
    assert (
        await store.owner_faces.get_desired_for_owner("owner-1")
    ).profile_revision_id == cleared.profile_revision_id
    superseded = await store.owner_faces.list_superseded_references("owner-1")
    assert {row.reference_id for row in superseded} == {row.reference_id for row in references}
    deleted = await store.owner_faces.delete_superseded_references(
        owner_id="owner-1",
        reference_ids=[row.reference_id for row in superseded],
    )
    assert deleted == 3
