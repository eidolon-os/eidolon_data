"""Real people within an Owner namespace, independent of Companions."""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    false,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column

from eidolon_data.db.base import Base


class PersonRow(Base):
    __tablename__ = "persons"
    person_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))
    deleted: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    identity_state: Mapped[str] = mapped_column(String(16), default="known", server_default="known")
    maturity: Mapped[str] = mapped_column(
        String(16), default="established", server_default="established"
    )
    recognition_state: Mapped[str] = mapped_column(
        String(16), default="active", server_default="active"
    )
    is_named: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    archived_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    revision: Mapped[int] = mapped_column(Integer)
    profile_json: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (
        Index("uq_persons_owner_person", "owner_id", "person_id", unique=True),
        CheckConstraint("status IN ('active', 'inactive')", name="person_status"),
        CheckConstraint("maturity IN ('candidate', 'established')", name="person_maturity"),
        CheckConstraint("recognition_state IN ('active', 'retained')", name="person_recognition_state"),
        CheckConstraint("revision > 0", name="person_revision_positive"),
    )


class PersonDeletionRow(Base):
    """Minimal idempotency receipt; no name, biography or biometric data."""

    __tablename__ = "person_deletions"
    person_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.owner_id", ondelete="CASCADE"))
    revision: Mapped[int] = mapped_column(Integer)
    realm_id: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16))
