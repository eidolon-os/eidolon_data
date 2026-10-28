"""Real people within an Owner namespace, independent of Companions."""

from sqlalchemy import JSON, Boolean, CheckConstraint, ForeignKey, Index, Integer, String, false
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
    revision: Mapped[int] = mapped_column(Integer)
    profile_json: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (
        Index("uq_persons_owner_person", "owner_id", "person_id", unique=True),
        CheckConstraint("status IN ('active', 'inactive')", name="person_status"),
        CheckConstraint("revision > 0", name="person_revision_positive"),
    )
