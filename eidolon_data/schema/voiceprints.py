"""Data owns speaker identity and Companion authorization, never inference."""

from typing import Any
from sqlalchemy import ForeignKey, Integer, JSON, String, Float, LargeBinary
from sqlalchemy.orm import Mapped, mapped_column
from eidolon_data.db.base import Base


class SpeakerProfileRow(Base):
    __tablename__ = "speaker_profiles"
    profile_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer)
    enrollment_id: Mapped[str] = mapped_column(String(64))
    template_json: Mapped[dict[str, Any]] = mapped_column(JSON)


class VoicePolicyRow(Base):
    __tablename__ = "companion_voice_policies"
    companion_id: Mapped[str] = mapped_column(
        ForeignKey("companions.companion_id", ondelete="CASCADE"), primary_key=True
    )
    mode: Mapped[str] = mapped_column(String(16), default="off")
    revision: Mapped[int] = mapped_column(Integer, default=0)


class SpeakerRecordingRow(Base):
    """One original successful trial, separate from the realtime template read path."""

    __tablename__ = "speaker_recordings"
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("speaker_profiles.profile_id", ondelete="CASCADE"), primary_key=True
    )
    pcm: Mapped[bytes] = mapped_column(LargeBinary)


class SpeakerBindingRow(Base):
    __tablename__ = "companion_speaker_bindings"
    companion_id: Mapped[str] = mapped_column(
        ForeignKey("companions.companion_id", ondelete="CASCADE"), primary_key=True
    )
    profile_id: Mapped[str] = mapped_column(
        ForeignKey("speaker_profiles.profile_id", ondelete="CASCADE"), primary_key=True
    )


class VoiceEnrollmentRow(Base):
    __tablename__ = "voice_enrollments"
    enrollment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    companion_id: Mapped[str] = mapped_column(
        ForeignKey("companions.companion_id", ondelete="CASCADE")
    )
    profile_id: Mapped[str] = mapped_column(String(64))
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    samples_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(16), default="collecting")
    expires_at: Mapped[float] = mapped_column(Float)
