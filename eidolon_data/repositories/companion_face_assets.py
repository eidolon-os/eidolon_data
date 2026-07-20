"""Versioned companion display-face (digital-human ``cond_image``) assets.

SQL stores only the opaque object-store key + integrity metadata; the JPEG
bytes live in :class:`LocalObjectStorage`.  At most one asset per companion is
``active``; setting a new face supersedes the prior active version (the same
revision-state model used by Owner Face Profiles), so history is preserved and
"the current face" is a single indexed lookup.
"""

from __future__ import annotations

import re
from uuid import uuid4

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from eidolon_data.db.base import utc_now
from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import CompanionFaceAssetRow, CompanionRow

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _validate_asset_inputs(
    *,
    content_type: str,
    size_bytes: int,
    sha256: str,
    storage_key: str,
) -> None:
    if content_type != "image/jpeg":
        raise ValueError("companion face asset must be image/jpeg")
    if size_bytes <= 0:
        raise ValueError("companion face asset size must be positive")
    if _SHA256_RE.fullmatch(sha256) is None:
        raise ValueError("companion face asset sha256 must be lowercase hexadecimal")
    if not storage_key or storage_key.startswith("/") or ".." in storage_key.split("/"):
        raise ValueError("companion face asset storage key must be a safe relative path")


class CompanionFaceAssetsRepository(Repository):
    async def set_face(
        self,
        *,
        companion_id: str,
        cond_storage_key: str,
        cond_content_type: str,
        cond_size_bytes: int,
        cond_sha256: str,
        width: int | None = None,
        height: int | None = None,
        source: str = "upload",
        meta: dict | None = None,
    ) -> CompanionFaceAssetRow:
        """Supersede the companion's current face and record a new active version.

        Returns the new active :class:`CompanionFaceAssetRow`.  Raises ``KeyError``
        when the companion does not exist and ``ValueError`` on invalid inputs or
        a concurrent-modification conflict (the caller should retry).
        """
        _validate_asset_inputs(
            content_type=cond_content_type,
            size_bytes=cond_size_bytes,
            sha256=cond_sha256,
            storage_key=cond_storage_key,
        )
        async with self._session_factory() as session:
            companion = await session.get(CompanionRow, companion_id)
            if companion is None:
                raise KeyError(f"companion not found: {companion_id}")
            now = utc_now()
            await session.execute(
                update(CompanionFaceAssetRow)
                .where(
                    CompanionFaceAssetRow.companion_id == companion_id,
                    CompanionFaceAssetRow.state == "active",
                )
                .values(state="superseded", updated_at=now)
            )
            max_version = await session.scalar(
                select(func.max(CompanionFaceAssetRow.version)).where(
                    CompanionFaceAssetRow.companion_id == companion_id
                )
            )
            row = CompanionFaceAssetRow(
                face_asset_id=f"cfa_{uuid4().hex}",
                companion_id=companion_id,
                owner_id=companion.owner_id,
                version=int(max_version or 0) + 1,
                state="active",
                source=source,
                cond_storage_key=cond_storage_key,
                cond_content_type=cond_content_type,
                cond_size_bytes=cond_size_bytes,
                cond_sha256=cond_sha256,
                width=width,
                height=height,
                meta_json=meta or {},
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ValueError(
                    "companion face asset changed concurrently or storage key is reused; retry"
                ) from exc
            await session.refresh(row)
            return row

    async def set_idle_status(
        self, face_asset_id: str, status: str, *, error: str | None = None
    ) -> None:
        """Move a face asset's idle-clip lifecycle (pending/generating/failed)."""
        if status not in {"none", "pending", "generating", "failed"}:
            raise ValueError(f"unsupported idle status transition: {status}")
        async with self._session_factory() as session:
            row = await session.get(CompanionFaceAssetRow, face_asset_id)
            if row is None:
                raise KeyError(f"companion face asset not found: {face_asset_id}")
            row.idle_status = status
            row.idle_error = error
            row.updated_at = utc_now()
            await session.commit()

    async def set_idle_clip(
        self,
        face_asset_id: str,
        *,
        storage_key: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
    ) -> CompanionFaceAssetRow:
        """Record a generated idle clip and mark the asset's idle status ready."""
        if size_bytes <= 0:
            raise ValueError("idle clip size must be positive")
        if _SHA256_RE.fullmatch(sha256) is None:
            raise ValueError("idle clip sha256 must be lowercase hexadecimal")
        if not storage_key or storage_key.startswith("/") or ".." in storage_key.split("/"):
            raise ValueError("idle clip storage key must be a safe relative path")
        async with self._session_factory() as session:
            row = await session.get(CompanionFaceAssetRow, face_asset_id)
            if row is None:
                raise KeyError(f"companion face asset not found: {face_asset_id}")
            row.idle_status = "ready"
            row.idle_storage_key = storage_key
            row.idle_content_type = content_type
            row.idle_size_bytes = size_bytes
            row.idle_sha256 = sha256
            row.idle_error = None
            row.updated_at = utc_now()
            await session.commit()
            await session.refresh(row)
            return row

    async def get_active(self, companion_id: str) -> CompanionFaceAssetRow | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(CompanionFaceAssetRow).where(
                    CompanionFaceAssetRow.companion_id == companion_id,
                    CompanionFaceAssetRow.state == "active",
                )
            )

    async def get(self, face_asset_id: str) -> CompanionFaceAssetRow | None:
        async with self._session_factory() as session:
            return await session.get(CompanionFaceAssetRow, face_asset_id)

    async def list_for_companion(self, companion_id: str) -> list[CompanionFaceAssetRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(CompanionFaceAssetRow)
                .where(CompanionFaceAssetRow.companion_id == companion_id)
                .order_by(CompanionFaceAssetRow.version.desc())
            )
            return list(rows)

    async def clear(self, companion_id: str) -> bool:
        """Supersede the active face so the companion reverts to the default avatar.

        Returns ``True`` when an active face was superseded, ``False`` when the
        companion already had none.
        """
        async with self._session_factory() as session:
            result = await session.execute(
                update(CompanionFaceAssetRow)
                .where(
                    CompanionFaceAssetRow.companion_id == companion_id,
                    CompanionFaceAssetRow.state == "active",
                )
                .values(state="superseded", updated_at=utc_now())
            )
            await session.commit()
            return int(result.rowcount or 0) > 0

    async def list_storage_keys_for_companion(self, companion_id: str) -> list[str]:
        """Every object-store key this companion owns (cond image + idle clip),
        for blob purge on delete."""
        async with self._session_factory() as session:
            result = await session.execute(
                select(
                    CompanionFaceAssetRow.cond_storage_key,
                    CompanionFaceAssetRow.idle_storage_key,
                ).where(CompanionFaceAssetRow.companion_id == companion_id)
            )
            keys: list[str] = []
            for cond_key, idle_key in result.all():
                keys.append(cond_key)
                if idle_key:
                    keys.append(idle_key)
            return keys
