"""An Owner's own voices (designed or cloned) and where each one was made.

Audio lives in object storage; rows hold ownership, provenance and pointers. A
provider voice is tracked from the moment it exists until its provider confirms
it is gone: as a binding while it can be used, then as a tombstone. Tombstones
carry no foreign key on purpose — they must outlive the Owner they came from.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from eidolon_data.db.base import Base, utc_now


class OwnerVoiceRow(Base):
    __tablename__ = "owner_voices"

    voice_asset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(64))
    method: Mapped[str] = mapped_column(String(16))
    state: Mapped[str] = mapped_column(String(16))
    revision: Mapped[int] = mapped_column(Integer, default=1)
    design_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    design_preview_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    consent_json: Mapped[dict[str, Any] | None] = mapped_column(nullable=True)
    reference_storage_key: Mapped[str | None] = mapped_column(String(256), nullable=True, unique=True)
    preview_storage_key: Mapped[str | None] = mapped_column(String(256), nullable=True, unique=True)
    create_operation_id: Mapped[str] = mapped_column(String(128))
    draft_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("owner_id", "create_operation_id", name="uq_owner_voice_operation"),
        CheckConstraint("method IN ('design', 'clone')", name="owner_voice_method"),
        CheckConstraint("state IN ('draft', 'active')", name="owner_voice_state"),
        CheckConstraint("revision > 0", name="owner_voice_revision_positive"),
    )


class OwnerVoiceBindingRow(Base):
    __tablename__ = "owner_voice_bindings"

    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_voice_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    voice_asset_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owner_voices.voice_asset_id", ondelete="CASCADE"), index=True
    )
    model: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    status_detail: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        UniqueConstraint("voice_asset_id", "provider", "model", name="uq_owner_voice_binding_model"),
        CheckConstraint(
            "status IN ('deploying', 'ready', 'rejected', 'failed')", name="owner_voice_binding_status"
        ),
    )


class ProviderVoiceTombstoneRow(Base):
    __tablename__ = "provider_voice_tombstones"

    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider_voice_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    model: Mapped[str] = mapped_column(String(128))
    reason: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        CheckConstraint(
            "reason IN ('deleted', 'replaced', 'owner_deleted', 'draft_expired')",
            name="provider_voice_tombstone_reason",
        ),
    )
