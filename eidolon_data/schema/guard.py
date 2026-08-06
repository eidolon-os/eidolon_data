"""Persistence rows for low-frequency Guard governance configuration."""

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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from eidolon_data.db.base import Base, utc_now

JsonDict = dict[str, Any]


class GuardBindingRow(Base):
    """A governed reference to an externally authoritative physical Device.

    ``device_id`` is opaque and has no foreign key: Hub owns admission and
    Kernel owns mounting. Data owns only the Owner/Companion policy binding.
    """

    __tablename__ = "guard_bindings"

    binding_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    guard_companion_id: Mapped[str] = mapped_column(String(64), index=True)
    device_id: Mapped[str] = mapped_column(String(128), index=True)
    state: Mapped[str] = mapped_column(String(16), default="active", index=True)
    policy_id: Mapped[str] = mapped_column(String(64), default="silent_presence")
    policy_revision: Mapped[int] = mapped_column(Integer, default=1)
    policy_json: Mapped[JsonDict] = mapped_column(default=dict)
    activated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    disabled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "guard_companion_id"],
            ["companions.owner_id", "companions.companion_id"],
            name="fk_guard_bindings_owner_guard_companion",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "state IN ('active', 'disabled', 'revoked')",
            name="guard_binding_state",
        ),
        CheckConstraint("policy_revision > 0", name="guard_policy_revision_positive"),
        Index(
            "uq_guard_bindings_guard_companion_active",
            "guard_companion_id",
            unique=True,
            sqlite_where=text("state = 'active'"),
            postgresql_where=text("state = 'active'"),
        ),
        Index(
            "uq_guard_bindings_device_active",
            "device_id",
            unique=True,
            sqlite_where=text("state = 'active'"),
            postgresql_where=text("state = 'active'"),
        ),
        Index("ix_guard_bindings_owner_state", "owner_id", "state"),
    )
