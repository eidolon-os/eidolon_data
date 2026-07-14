from __future__ import annotations

import hashlib

import pytest

from eidolon_data.services.object_storage import LocalObjectStorage


def test_local_object_storage_is_atomic_and_content_verified(tmp_path) -> None:
    storage = LocalObjectStorage(tmp_path / "objects")
    content = b"normalized-jpeg"
    digest = hashlib.sha256(content).hexdigest()

    assert storage.put("owner-1/face/ref.jpg", content, expected_sha256=digest) == digest
    assert storage.get("owner-1/face/ref.jpg") == content
    assert storage.exists("owner-1/face/ref.jpg") is True
    assert (tmp_path / "objects/owner-1/face/ref.jpg").stat().st_mode & 0o777 == 0o600

    storage.delete("owner-1/face/ref.jpg")
    assert storage.exists("owner-1/face/ref.jpg") is False


def test_local_object_storage_rejects_path_escape_and_hash_mismatch(tmp_path) -> None:
    storage = LocalObjectStorage(tmp_path / "objects")

    with pytest.raises(ValueError, match="inside the object store"):
        storage.put("../escape.jpg", b"data")
    with pytest.raises(ValueError, match="sha256"):
        storage.put("owner/ref.jpg", b"data", expected_sha256="0" * 64)
