"""Data owns Person voiceprint evidence, never inference or conversation admission."""

from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from eidolon_data.db.base import Base


class SpeakerProfileRow(Base):
    __tablename__ = "speaker_profiles"
    profile_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    person_id: Mapped[str | None] = mapped_column(
        ForeignKey("persons.person_id", ondelete="CASCADE"), nullable=True, index=True
    )
    display_name: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer)
    enrollment_id: Mapped[str] = mapped_column(String(64))
    template_json: Mapped[dict[str, Any]] = mapped_column(JSON)


class SpeakerRecordingRow(Base):
    """One original successful trial, separate from the realtime template read path."""

    __tablename__ = "speaker_recordings"
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("speaker_profiles.profile_id", ondelete="CASCADE"), primary_key=True
    )
    pcm: Mapped[bytes] = mapped_column(LargeBinary)


class VoiceEnrollmentRow(Base):
    __tablename__ = "voice_enrollments"
    enrollment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    person_id: Mapped[str | None] = mapped_column(
        ForeignKey("persons.person_id", ondelete="CASCADE"), nullable=True, index=True
    )
    profile_id: Mapped[str] = mapped_column(String(64))
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    samples_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(16), default="collecting")
    expires_at: Mapped[float] = mapped_column(Float)
