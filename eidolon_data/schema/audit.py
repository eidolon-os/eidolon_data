"""Persistence row for the authority-local global-audit hand-off."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from eidolon_data.db.base import Base, utc_now

JsonDict = dict[str, Any]


class AuditOutboxRow(Base):
    __tablename__ = "audit_outbox"

    outbox_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(String(64), unique=True)
    producer: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(16))
    owner_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subject_type: Mapped[str] = mapped_column(String(64))
    subject_id: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(128))
    outcome: Mapped[str] = mapped_column(String(16), default="success")
    severity: Mapped[str] = mapped_column(String(16), default="info")
    reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    trace_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    data_classification: Mapped[str] = mapped_column(String(16), default="safe")
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    payload_json: Mapped[JsonDict] = mapped_column(default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        CheckConstraint(
            "category IN ('governance', 'receipt')",
            name="audit_outbox_category",
        ),
        CheckConstraint(
            "outcome IN ('success', 'failure', 'denied', 'deferred')",
            name="audit_outbox_outcome",
        ),
        CheckConstraint(
            "severity IN ('info', 'warn', 'error', 'critical')",
            name="audit_outbox_severity",
        ),
        CheckConstraint("attempt_count >= 0", name="audit_outbox_attempt_count"),
        Index(
            "ix_audit_outbox_pending",
            "published_at",
            "next_attempt_at",
            "outbox_id",
        ),
    )
