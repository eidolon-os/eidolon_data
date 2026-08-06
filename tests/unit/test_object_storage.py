from __future__ import annotations

import hashlib

import pytest

from eidolon_data.services.object_storage import LocalObjectStorage

pytestmark = pytest.mark.unit


def test_object_storage_round_trip_replace_and_delete(tmp_path) -> None:
    storage = LocalObjectStorage(tmp_path / "objects")
    key = "owner-1/faces/front.jpg"
    assert storage.put(key, b"first") == hashlib.sha256(b"first").hexdigest()
    assert storage.get(key) == b"first"
    storage.put(key, b"second")
    assert storage.get(key) == b"second"
    assert (tmp_path / "objects" / key).stat().st_mode & 0o777 == 0o600
    storage.delete(key)
    assert not storage.exists(key)
    storage.delete(key)


@pytest.mark.parametrize("key", ["", "/absolute", "../escape", "owner/../../escape"])
def test_object_storage_rejects_unsafe_keys(tmp_path, key: str) -> None:
    storage = LocalObjectStorage(tmp_path / "objects")
    with pytest.raises(ValueError, match=r"safe relative|inside"):
        storage.put(key, b"content")


def test_object_storage_rejects_empty_and_digest_mismatch(tmp_path) -> None:
    storage = LocalObjectStorage(tmp_path / "objects")
    with pytest.raises(ValueError, match="must not be empty"):
        storage.put("safe/key", b"")
    with pytest.raises(ValueError, match="sha256"):
        storage.put("safe/key", b"content", expected_sha256="0" * 64)
    assert not storage.exists("safe/key")
