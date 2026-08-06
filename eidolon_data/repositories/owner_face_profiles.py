"""Normalized Owner Face Profile revisions, references, and desired fan-out."""

from __future__ import annotations

import re
from uuid import uuid4

from eidolon_sdk.biz.guard import (
    OWNER_FACE_PROFILE_MAX_REFERENCES,
    OWNER_FACE_PROFILE_MIN_REFERENCES,
    OWNER_FACE_PROFILE_REQUIRED_POSES,
)
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from eidolon_data.audit.outbox import AuditOutboxRepository
from eidolon_data.db.base import utc_now
from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import (
    GuardBindingRow,
    GuardOwnerFaceProfileDeliveryRow,
    OwnerFaceProfileRevisionRow,
    OwnerFaceReferenceRow,
    OwnerRow,
)

_POSES = ("front", "left", "right", "down", "up")
_POSE_ORDER = {pose: ordinal for ordinal, pose in enumerate(_POSES)}
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_PROFILE_REVISION_CONFLICT_MARKERS = (
    "uq_owner_face_profile_revision",
    "uq_owner_face_owner_revision",
    "uq_owner_face_profile_owner_desired",
    "owner_face_profile_revisions.profile_id, owner_face_profile_revisions.revision",
    "owner_face_profile_revisions.owner_id, owner_face_profile_revisions.revision",
    "unique constraint failed: owner_face_profile_revisions.owner_id",
)


def _is_profile_revision_conflict(exc: IntegrityError) -> bool:
    message = str(exc.orig).lower()
    return any(marker in message for marker in _PROFILE_REVISION_CONFLICT_MARKERS)


class OwnerFaceProfilesRepository(Repository):
    async def create_draft(
        self,
        *,
        owner_id: str,
        model_id: str,
        preprocessing_version: str,
    ) -> OwnerFaceProfileRevisionRow:
        if not model_id or not preprocessing_version:
            raise ValueError("owner face profile requires model compatibility metadata")
        async with self._session_factory() as session:
            if await session.get(OwnerRow, owner_id) is None:
                raise KeyError(f"owner not found: {owner_id}")
            latest = await session.scalar(
                select(OwnerFaceProfileRevisionRow)
                .where(OwnerFaceProfileRevisionRow.owner_id == owner_id)
                .order_by(OwnerFaceProfileRevisionRow.revision.desc())
            )
            profile_id = latest.profile_id if latest is not None else f"ofp_{uuid4().hex}"
            revision = (latest.revision if latest is not None else 0) + 1
            now = utc_now()
            await session.execute(
                update(OwnerFaceProfileRevisionRow)
                .where(
                    OwnerFaceProfileRevisionRow.owner_id == owner_id,
                    OwnerFaceProfileRevisionRow.state == "draft",
                )
                .values(state="superseded", updated_at=now)
            )
            row = OwnerFaceProfileRevisionRow(
                profile_revision_id=f"ofpr_{uuid4().hex}",
                profile_id=profile_id,
                owner_id=owner_id,
                revision=revision,
                state="draft",
                desired_state="active",
                model_id=model_id,
                preprocessing_version=preprocessing_version,
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            session.add(
                AuditOutboxRepository.build_row(
                    producer="eidolon-guard",
                    category="governance",
                    owner_id=owner_id,
                    subject_type="owner_face_profile",
                    subject_id=profile_id,
                    action="guard.owner_face_profile.draft_created",
                    data_classification="sensitive",
                    payload={
                        "profile_revision": revision,
                        "model_id": model_id,
                        "preprocessing_version": preprocessing_version,
                    },
                )
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                if _is_profile_revision_conflict(exc):
                    raise ValueError("owner face profile changed concurrently; retry") from exc
                raise
            await session.refresh(row)
            return row

    async def add_reference(
        self,
        *,
        profile_revision_id: str,
        pose: str,
        content_type: str,
        size_bytes: int,
        sha256: str,
        storage_key: str,
    ) -> OwnerFaceReferenceRow:
        if pose not in _POSE_ORDER:
            raise ValueError(f"unsupported owner face pose: {pose}")
        if content_type != "image/jpeg":
            raise ValueError("owner face reference must be image/jpeg")
        if size_bytes <= 0:
            raise ValueError("owner face reference size must be positive")
        if _SHA256_RE.fullmatch(sha256) is None:
            raise ValueError("owner face reference sha256 must be lowercase hexadecimal")
        if not storage_key or storage_key.startswith("/") or ".." in storage_key.split("/"):
            raise ValueError("owner face reference storage key must be a safe relative path")
        async with self._session_factory() as session:
            profile = await session.get(OwnerFaceProfileRevisionRow, profile_revision_id)
            if profile is None:
                raise KeyError(f"owner face profile revision not found: {profile_revision_id}")
            if profile.state != "draft":
                raise ValueError("owner face references can only be added to a draft")
            existing_count = await session.scalar(
                select(func.count(OwnerFaceReferenceRow.reference_id)).where(
                    OwnerFaceReferenceRow.profile_revision_id == profile_revision_id
                )
            )
            if int(existing_count or 0) >= OWNER_FACE_PROFILE_MAX_REFERENCES:
                raise ValueError("owner face profile already has five references")
            row = OwnerFaceReferenceRow(
                reference_id=f"ofr_{uuid4().hex}",
                profile_revision_id=profile_revision_id,
                pose=pose,
                content_type=content_type,
                size_bytes=size_bytes,
                sha256=sha256,
                storage_key=storage_key,
            )
            session.add(row)
            session.add(
                AuditOutboxRepository.build_row(
                    producer="eidolon-guard",
                    category="governance",
                    owner_id=profile.owner_id,
                    subject_type="owner_face_profile",
                    subject_id=profile.profile_id,
                    action="guard.owner_face_profile.reference_added",
                    data_classification="sensitive",
                    payload={
                        "profile_revision": profile.revision,
                        "reference_id": row.reference_id,
                        "pose": row.pose,
                    },
                )
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ValueError("owner face pose or storage key is already present") from exc
            await session.refresh(row)
            return row

    async def activate(self, profile_revision_id: str) -> OwnerFaceProfileRevisionRow:
        async with self._session_factory() as session:
            profile = await session.get(OwnerFaceProfileRevisionRow, profile_revision_id)
            if profile is None:
                raise KeyError(f"owner face profile revision not found: {profile_revision_id}")
            if profile.state != "draft" or profile.desired_state != "active":
                raise ValueError("only an active draft owner face profile can be activated")
            references = list(
                await session.scalars(
                    select(OwnerFaceReferenceRow).where(
                        OwnerFaceReferenceRow.profile_revision_id == profile_revision_id
                    )
                )
            )
            poses = {reference.pose for reference in references}
            if not (
                OWNER_FACE_PROFILE_MIN_REFERENCES
                <= len(references)
                <= OWNER_FACE_PROFILE_MAX_REFERENCES
            ):
                raise ValueError("owner face profile requires three to five references")
            if not OWNER_FACE_PROFILE_REQUIRED_POSES.issubset(poses):
                raise ValueError("owner face profile requires front, left, and right references")

            now = utc_now()
            await session.execute(
                update(OwnerFaceProfileRevisionRow)
                .where(
                    OwnerFaceProfileRevisionRow.owner_id == profile.owner_id,
                    OwnerFaceProfileRevisionRow.state == "desired",
                )
                .values(state="superseded", updated_at=now)
            )
            profile.state = "desired"
            profile.activated_at = now
            profile.updated_at = now
            bindings = list(
                await session.scalars(
                    select(GuardBindingRow).where(
                        GuardBindingRow.owner_id == profile.owner_id,
                        GuardBindingRow.state == "active",
                    )
                )
            )
            for binding in bindings:
                await _enqueue_profile_delivery(session, binding, profile)
            session.add(
                AuditOutboxRepository.build_row(
                    producer="eidolon-guard",
                    category="governance",
                    owner_id=profile.owner_id,
                    subject_type="owner_face_profile",
                    subject_id=profile.profile_id,
                    action="guard.owner_face_profile.desired",
                    data_classification="sensitive",
                    payload={
                        "profile_revision": profile.revision,
                        "reference_count": len(references),
                    },
                )
            )
            await session.commit()
            await session.refresh(profile)
            return profile

    async def clear(self, *, owner_id: str) -> OwnerFaceProfileRevisionRow:
        async with self._session_factory() as session:
            if await session.get(OwnerRow, owner_id) is None:
                raise KeyError(f"owner not found: {owner_id}")
            latest = await session.scalar(
                select(OwnerFaceProfileRevisionRow)
                .where(OwnerFaceProfileRevisionRow.owner_id == owner_id)
                .order_by(OwnerFaceProfileRevisionRow.revision.desc())
            )
            profile_id = latest.profile_id if latest is not None else f"ofp_{uuid4().hex}"
            revision = (latest.revision if latest is not None else 0) + 1
            now = utc_now()
            await session.execute(
                update(OwnerFaceProfileRevisionRow)
                .where(
                    OwnerFaceProfileRevisionRow.owner_id == owner_id,
                    OwnerFaceProfileRevisionRow.state.in_({"draft", "desired"}),
                )
                .values(state="superseded", updated_at=now)
            )
            profile = OwnerFaceProfileRevisionRow(
                profile_revision_id=f"ofpr_{uuid4().hex}",
                profile_id=profile_id,
                owner_id=owner_id,
                revision=revision,
                state="desired",
                desired_state="cleared",
                model_id=None,
                preprocessing_version=None,
                activated_at=now,
                created_at=now,
                updated_at=now,
            )
            session.add(profile)
            bindings = list(
                await session.scalars(
                    select(GuardBindingRow).where(
                        GuardBindingRow.owner_id == owner_id,
                        GuardBindingRow.state == "active",
                    )
                )
            )
            for binding in bindings:
                await _enqueue_profile_delivery(session, binding, profile)
            session.add(
                AuditOutboxRepository.build_row(
                    producer="eidolon-guard",
                    category="governance",
                    owner_id=owner_id,
                    subject_type="owner_face_profile",
                    subject_id=profile_id,
                    action="guard.owner_face_profile.cleared",
                    data_classification="sensitive",
                    payload={"profile_revision": revision},
                )
            )
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                if _is_profile_revision_conflict(exc):
                    raise ValueError("owner face profile changed concurrently; retry") from exc
                raise
            await session.refresh(profile)
            return profile

    async def get_revision(self, profile_revision_id: str) -> OwnerFaceProfileRevisionRow | None:
        async with self._session_factory() as session:
            return await session.get(OwnerFaceProfileRevisionRow, profile_revision_id)

    async def get_desired_for_owner(self, owner_id: str) -> OwnerFaceProfileRevisionRow | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(OwnerFaceProfileRevisionRow).where(
                    OwnerFaceProfileRevisionRow.owner_id == owner_id,
                    OwnerFaceProfileRevisionRow.state == "desired",
                )
            )

    async def list_references(self, profile_revision_id: str) -> list[OwnerFaceReferenceRow]:
        async with self._session_factory() as session:
            rows = list(
                await session.scalars(
                    select(OwnerFaceReferenceRow).where(
                        OwnerFaceReferenceRow.profile_revision_id == profile_revision_id
                    )
                )
            )
            return sorted(rows, key=lambda row: _POSE_ORDER[row.pose])

    async def list_storage_keys_for_owner(self, owner_id: str) -> list[str]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(OwnerFaceReferenceRow.storage_key)
                .join(
                    OwnerFaceProfileRevisionRow,
                    OwnerFaceProfileRevisionRow.profile_revision_id
                    == OwnerFaceReferenceRow.profile_revision_id,
                )
                .where(OwnerFaceProfileRevisionRow.owner_id == owner_id)
            )
            return list(rows)

    async def list_superseded_references(
        self,
        owner_id: str,
    ) -> list[OwnerFaceReferenceRow]:
        """Return inaccessible media that can be physically purged."""
        async with self._session_factory() as session:
            return list(
                await session.scalars(
                    select(OwnerFaceReferenceRow)
                    .join(
                        OwnerFaceProfileRevisionRow,
                        OwnerFaceProfileRevisionRow.profile_revision_id
                        == OwnerFaceReferenceRow.profile_revision_id,
                    )
                    .where(
                        OwnerFaceProfileRevisionRow.owner_id == owner_id,
                        OwnerFaceProfileRevisionRow.state == "superseded",
                    )
                )
            )

    async def delete_superseded_references(
        self,
        *,
        owner_id: str,
        reference_ids: list[str],
    ) -> int:
        """Delete metadata only after its corresponding media was removed."""
        if not reference_ids:
            return 0
        async with self._session_factory() as session:
            superseded_ids = select(OwnerFaceProfileRevisionRow.profile_revision_id).where(
                OwnerFaceProfileRevisionRow.owner_id == owner_id,
                OwnerFaceProfileRevisionRow.state == "superseded",
            )
            result = await session.execute(
                delete(OwnerFaceReferenceRow).where(
                    OwnerFaceReferenceRow.reference_id.in_(reference_ids),
                    OwnerFaceReferenceRow.profile_revision_id.in_(superseded_ids),
                )
            )
            await session.commit()
            return int(result.rowcount or 0)

    async def get_reference_for_active_device(
        self,
        *,
        device_id: str,
        reference_id: str,
    ) -> OwnerFaceReferenceRow | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(OwnerFaceReferenceRow)
                .join(
                    OwnerFaceProfileRevisionRow,
                    OwnerFaceProfileRevisionRow.profile_revision_id
                    == OwnerFaceReferenceRow.profile_revision_id,
                )
                .join(
                    GuardBindingRow,
                    GuardBindingRow.owner_id == OwnerFaceProfileRevisionRow.owner_id,
                )
                .where(
                    OwnerFaceReferenceRow.reference_id == reference_id,
                    OwnerFaceProfileRevisionRow.state == "desired",
                    OwnerFaceProfileRevisionRow.desired_state == "active",
                    GuardBindingRow.device_id == device_id,
                    GuardBindingRow.state == "active",
                )
            )


async def _enqueue_profile_delivery(
    session,
    binding: GuardBindingRow,
    profile: OwnerFaceProfileRevisionRow,
) -> GuardOwnerFaceProfileDeliveryRow:
    await session.execute(
        update(GuardOwnerFaceProfileDeliveryRow)
        .where(
            GuardOwnerFaceProfileDeliveryRow.binding_id == binding.binding_id,
            GuardOwnerFaceProfileDeliveryRow.status.in_(
                {"pending", "dispatching", "dispatched"}
            ),
        )
        .values(status="superseded", lease_expires_at=None, updated_at=utc_now())
    )
    delivery = GuardOwnerFaceProfileDeliveryRow(
        delivery_id=f"gfpd_{uuid4().hex}",
        binding_id=binding.binding_id,
        profile_revision_id=profile.profile_revision_id,
        status="pending",
    )
    session.add(delivery)
    return delivery


async def enqueue_desired_profile_for_binding(session, binding: GuardBindingRow) -> None:
    profile = await session.scalar(
        select(OwnerFaceProfileRevisionRow).where(
            OwnerFaceProfileRevisionRow.owner_id == binding.owner_id,
            OwnerFaceProfileRevisionRow.state == "desired",
        )
    )
    if profile is not None:
        await _enqueue_profile_delivery(session, binding, profile)
