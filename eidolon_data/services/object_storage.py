"""Local encrypted-volume-friendly object storage adapter.

SQL stores only owner-scoped metadata and opaque keys.  This adapter owns all
filesystem path validation and atomic file replacement so Admin and Hub do not
grow independent storage implementations.  Deployments may place the root on
an encrypted volume or replace this adapter behind the same small interface.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import uuid4


class LocalObjectStorage:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser().resolve()

    @property
    def root(self) -> Path:
        return self._root

    def put(self, storage_key: str, data: bytes, *, expected_sha256: str | None = None) -> str:
        if not data:
            raise ValueError("storage object must not be empty")
        digest = hashlib.sha256(data).hexdigest()
        if expected_sha256 is not None and digest != expected_sha256:
            raise ValueError("storage object sha256 does not match content")
        target = self._resolve(storage_key)
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("xb") as handle:
                os.chmod(temporary, 0o600)
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
            os.chmod(target, 0o600)
        finally:
            temporary.unlink(missing_ok=True)
        return digest

    def get(self, storage_key: str) -> bytes:
        return self._resolve(storage_key).read_bytes()

    def delete(self, storage_key: str) -> None:
        self._resolve(storage_key).unlink(missing_ok=True)

    def exists(self, storage_key: str) -> bool:
        return self._resolve(storage_key).is_file()

    def _resolve(self, storage_key: str) -> Path:
        if not storage_key or storage_key.startswith("/"):
            raise ValueError("storage key must be a safe relative path")
        candidate = (self._root / storage_key).resolve()
        if candidate == self._root or self._root not in candidate.parents:
            raise ValueError("storage key must stay inside the object store")
        return candidate
