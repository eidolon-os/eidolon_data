"""Persistence rows for governed asset metadata.

Large media bytes live in object storage. These rows contain only versioned
ownership, integrity metadata, and desired-state pointers.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from eidolon_data.db.base import Base, utc_now

JsonDict = dict[str, Any]


class CompanionFaceAssetRow(Base):
    __tablename__ = "companion_face_assets"

    face_asset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    companion_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companions.companion_id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16), default="active", index=True)
    source: Mapped[str] = mapped_column(String(32), default="upload")
    cond_storage_key: Mapped[str] = mapped_column(String(256), unique=True)
    cond_content_type: Mapped[str] = mapped_column(String(32))
    cond_size_bytes: Mapped[int] = mapped_column(Integer)
    cond_sha256: Mapped[str] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    idle_status: Mapped[str] = mapped_column(String(16), default="none")
    idle_storage_key: Mapped[str | None] = mapped_column(String(256), unique=True)
    idle_content_type: Mapped[str | None] = mapped_column(String(64))
    idle_size_bytes: Mapped[int | None] = mapped_column(Integer)
    idle_sha256: Mapped[str | None] = mapped_column(String(64))
    idle_error: Mapped[str | None] = mapped_column(Text)
    meta_json: Mapped[JsonDict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("companion_id", "version", name="uq_companion_face_asset_version"),
        ForeignKeyConstraint(
            ["owner_id", "companion_id"],
            ["companions.owner_id", "companions.companion_id"],
            name="fk_companion_face_assets_owner_companion",
            ondelete="CASCADE",
        ),
        Index(
            "uq_companion_face_asset_active",
            "companion_id",
            unique=True,
            sqlite_where=text("state = 'active'"),
            postgresql_where=text("state = 'active'"),
        ),
        CheckConstraint("version > 0", name="companion_face_asset_version_positive"),
        CheckConstraint(
            "state IN ('active', 'superseded')",
            name="companion_face_asset_state",
        ),
        CheckConstraint(
            "cond_content_type = 'image/jpeg'",
            name="companion_face_asset_content_type",
        ),
        CheckConstraint(
            "cond_size_bytes > 0",
            name="companion_face_asset_size_positive",
        ),
        CheckConstraint(
            "idle_status IN ('none', 'pending', 'generating', 'ready', 'failed')",
            name="companion_face_asset_idle_status",
        ),
    )


class OwnerFaceProfileRevisionRow(Base):
    __tablename__ = "owner_face_profile_revisions"

    profile_revision_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(64), index=True)
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16), default="draft", index=True)
    desired_state: Mapped[str] = mapped_column(String(16), default="active")
    model_id: Mapped[str | None] = mapped_column(String(96))
    preprocessing_version: Mapped[str | None] = mapped_column(String(96))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("profile_id", "revision", name="uq_owner_face_profile_revision"),
        UniqueConstraint("owner_id", "revision", name="uq_owner_face_owner_revision"),
        Index(
            "uq_owner_face_profile_owner_desired",
            "owner_id",
            unique=True,
            sqlite_where=text("state = 'desired'"),
            postgresql_where=text("state = 'desired'"),
        ),
        CheckConstraint("revision > 0", name="owner_face_profile_revision_positive"),
        CheckConstraint(
            "state IN ('draft', 'desired', 'superseded')",
            name="owner_face_profile_state",
        ),
        CheckConstraint(
            "desired_state IN ('active', 'cleared')",
            name="owner_face_profile_desired_state",
        ),
        CheckConstraint(
            "(desired_state = 'active' AND model_id IS NOT NULL "
            "AND preprocessing_version IS NOT NULL) OR "
            "(desired_state = 'cleared' AND model_id IS NULL "
            "AND preprocessing_version IS NULL)",
            name="owner_face_profile_model_state",
        ),
    )


class OwnerFaceReferenceRow(Base):
    __tablename__ = "owner_face_references"

    reference_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    profile_revision_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("owner_face_profile_revisions.profile_revision_id", ondelete="CASCADE"),
        index=True,
    )
    pose: Mapped[str] = mapped_column(String(16))
    content_type: Mapped[str] = mapped_column(String(32))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(256), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("profile_revision_id", "pose", name="uq_owner_face_reference_pose"),
        CheckConstraint(
            "pose IN ('front', 'left', 'right', 'down', 'up')",
            name="owner_face_reference_pose",
        ),
        CheckConstraint(
            "content_type = 'image/jpeg'",
            name="owner_face_reference_content_type",
        ),
        CheckConstraint("size_bytes > 0", name="owner_face_reference_size_positive"),
    )
