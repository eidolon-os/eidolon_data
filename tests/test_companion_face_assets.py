from __future__ import annotations

import hashlib

import pytest

from eidolon_data import DataSettings, DataStore


@pytest.fixture
async def store(tmp_path):
    value = DataStore.open(
        DataSettings(
            sqlite_path=str(tmp_path / "companion-face.sqlite3"),
            object_store_path=str(tmp_path / "objects"),
        )
    )
    await value.init_schema()
    try:
        yield value
    finally:
        await value.close()


async def _companion(store: DataStore, *, owner_id: str, companion_id: str):
    await store.owners.create(owner_id=owner_id, display_name=owner_id)
    return await store.companions.create(
        companion_id=companion_id, owner_id=owner_id, display_name=companion_id
    )


async def _put_face(store: DataStore, *, owner_id: str, companion_id: str, marker: bytes):
    content = b"\xff\xd8\xff" + marker  # JPEG SOI prefix, opaque body for the test
    digest = hashlib.sha256(content).hexdigest()
    storage_key = f"{owner_id}/companion-avatar/{companion_id}/{digest[:12]}.jpg"
    store.object_storage.put(storage_key, content, expected_sha256=digest)
    row = await store.companion_face_assets.set_face(
        companion_id=companion_id,
        cond_storage_key=storage_key,
        cond_content_type="image/jpeg",
        cond_size_bytes=len(content),
        cond_sha256=digest,
        width=448,
        height=448,
    )
    return row, content


async def test_set_face_versions_and_supersedes(store: DataStore) -> None:
    await _companion(store, owner_id="owner-1", companion_id="c-1")

    v1, _ = await _put_face(store, owner_id="owner-1", companion_id="c-1", marker=b"one")
    assert v1.version == 1 and v1.state == "active" and v1.owner_id == "owner-1"

    active = await store.companion_face_assets.get_active("c-1")
    assert active is not None and active.face_asset_id == v1.face_asset_id

    v2, content2 = await _put_face(store, owner_id="owner-1", companion_id="c-1", marker=b"two")
    assert v2.version == 2 and v2.state == "active"

    active = await store.companion_face_assets.get_active("c-1")
    assert active is not None and active.face_asset_id == v2.face_asset_id
    # The prior version is retained but superseded — exactly one active row.
    history = await store.companion_face_assets.list_for_companion("c-1")
    assert [(row.version, row.state) for row in history] == [(2, "active"), (1, "superseded")]
    # cond bytes resolve from the object store for the active version.
    assert store.object_storage.get(active.cond_storage_key) == content2


async def test_get_active_none_and_clear(store: DataStore) -> None:
    await _companion(store, owner_id="owner-1", companion_id="c-1")
    assert await store.companion_face_assets.get_active("c-1") is None

    await _put_face(store, owner_id="owner-1", companion_id="c-1", marker=b"x")
    assert await store.companion_face_assets.clear("c-1") is True
    assert await store.companion_face_assets.get_active("c-1") is None
    # Clearing again is a no-op (nothing active).
    assert await store.companion_face_assets.clear("c-1") is False


async def test_set_face_rejects_invalid_inputs(store: DataStore) -> None:
    await _companion(store, owner_id="owner-1", companion_id="c-1")
    good = dict(
        companion_id="c-1",
        cond_storage_key="owner-1/companion-avatar/c-1/a.jpg",
        cond_content_type="image/jpeg",
        cond_size_bytes=10,
        cond_sha256="a" * 64,
    )
    with pytest.raises(ValueError, match="image/jpeg"):
        await store.companion_face_assets.set_face(**{**good, "cond_content_type": "image/png"})
    with pytest.raises(ValueError, match="size must be positive"):
        await store.companion_face_assets.set_face(**{**good, "cond_size_bytes": 0})
    with pytest.raises(ValueError, match="lowercase hexadecimal"):
        await store.companion_face_assets.set_face(**{**good, "cond_sha256": "ZZZ"})
    with pytest.raises(ValueError, match="safe relative path"):
        await store.companion_face_assets.set_face(**{**good, "cond_storage_key": "/etc/passwd"})


async def test_set_face_unknown_companion(store: DataStore) -> None:
    with pytest.raises(KeyError):
        await store.companion_face_assets.set_face(
            companion_id="missing",
            cond_storage_key="o/companion-avatar/missing/a.jpg",
            cond_content_type="image/jpeg",
            cond_size_bytes=10,
            cond_sha256="a" * 64,
        )


async def test_deletion_returns_storage_keys_and_purges_rows(store: DataStore) -> None:
    await _companion(store, owner_id="owner-1", companion_id="c-1")
    v1, _ = await _put_face(store, owner_id="owner-1", companion_id="c-1", marker=b"one")
    v2, _ = await _put_face(store, owner_id="owner-1", companion_id="c-1", marker=b"two")

    result = await store.companion_deletion.delete_companion(
        owner_id="owner-1", companion_id="c-1", allow_master=True
    )
    assert set(result.face_asset_storage_keys) == {v1.cond_storage_key, v2.cond_storage_key}
    assert result.counts.get("companion_face_assets") == 2
    assert await store.companion_face_assets.list_for_companion("c-1") == []


async def test_idle_clip_lifecycle_and_gc(store: DataStore) -> None:
    await _companion(store, owner_id="owner-1", companion_id="c-1")
    asset, _ = await _put_face(store, owner_id="owner-1", companion_id="c-1", marker=b"x")
    assert asset.idle_status == "none"

    await store.companion_face_assets.set_idle_status(asset.face_asset_id, "generating")
    active = await store.companion_face_assets.get_active("c-1")
    assert active is not None and active.idle_status == "generating"

    idle_key = "owner-1/companion-avatar/c-1/idle-x.mp4"
    idle_bytes = b"\x00\x00\x00\x18ftypmp42fake-fmp4"
    store.object_storage.put(idle_key, idle_bytes)
    ready = await store.companion_face_assets.set_idle_clip(
        asset.face_asset_id,
        storage_key=idle_key,
        content_type="video/mp4",
        size_bytes=len(idle_bytes),
        sha256=hashlib.sha256(idle_bytes).hexdigest(),
    )
    assert ready.idle_status == "ready" and ready.idle_storage_key == idle_key

    # GC lists both the cond image and the idle clip.
    keys = await store.companion_face_assets.list_storage_keys_for_companion("c-1")
    assert idle_key in keys and asset.cond_storage_key in keys

    await store.companion_face_assets.set_idle_status(
        asset.face_asset_id, "failed", error="ditto unreachable"
    )
    failed = await store.companion_face_assets.get_active("c-1")
    assert failed is not None and failed.idle_status == "failed"
    assert failed.idle_error == "ditto unreachable"


async def test_deletion_collects_idle_clip_keys(store: DataStore) -> None:
    await _companion(store, owner_id="owner-1", companion_id="c-1")
    asset, _ = await _put_face(store, owner_id="owner-1", companion_id="c-1", marker=b"y")
    idle_key = "owner-1/companion-avatar/c-1/idle-y.mp4"
    store.object_storage.put(idle_key, b"idle-clip-bytes")
    await store.companion_face_assets.set_idle_clip(
        asset.face_asset_id,
        storage_key=idle_key,
        content_type="video/mp4",
        size_bytes=15,
        sha256=hashlib.sha256(b"idle-clip-bytes").hexdigest(),
    )

    result = await store.companion_deletion.delete_companion(
        owner_id="owner-1", companion_id="c-1", allow_master=True
    )
    assert idle_key in result.face_asset_storage_keys
    assert asset.cond_storage_key in result.face_asset_storage_keys
