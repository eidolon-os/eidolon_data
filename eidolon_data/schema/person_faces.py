"""One versioned photo slot per Person, independent of recognition runtime."""
from sqlalchemy import ForeignKeyConstraint, String, Integer, JSON, Text
from sqlalchemy.orm import Mapped, mapped_column
from eidolon_data.db.base import Base

class PersonFaceRow(Base):
    __tablename__ = "person_face_profiles"
    __table_args__ = (ForeignKeyConstraint(
        ["owner_id", "person_id"], ["persons.owner_id", "persons.person_id"],
        name="fk_person_face_owner_person", ondelete="CASCADE"),)
    person_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(256), unique=True)
    sha256: Mapped[str | None] = mapped_column(String(64))
    model_id: Mapped[str | None] = mapped_column(String(128))
    weights_hash: Mapped[str | None] = mapped_column(String(64))
    preprocessing_version: Mapped[str | None] = mapped_column(String(128))
    feature_schema_version: Mapped[int | None] = mapped_column(Integer)
    features_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # The Owner's separate agreement to derive a feature from this photo: what
    # was shown, which wording, and when the Host recorded it (ISO-8601 UTC).
    recognition_consent_version: Mapped[str | None] = mapped_column(String(64))
    recognition_consent_statement: Mapped[str | None] = mapped_column(Text)
    recognition_consent_at: Mapped[str | None] = mapped_column(String(40))
