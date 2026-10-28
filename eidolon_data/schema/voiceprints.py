"""Data owns Person voiceprint evidence, never inference or conversation admission."""

from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    LargeBinary,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from eidolon_data.db.base import Base


class SpeakerProfileRow(Base):
    __tablename__ = "speaker_profiles"
    __table_args__ = (
        CheckConstraint("(origin='enrollment' AND enrollment_id IS NOT NULL AND source_observation_id IS NULL) OR (origin='observation' AND enrollment_id IS NULL AND source_observation_id IS NOT NULL)", name="speaker_profile_origin"),
        Index("uq_speaker_profile_person", "owner_id", "person_id", unique=True),
        ForeignKeyConstraint(
            ["owner_id", "person_id"],
            ["persons.owner_id", "persons.person_id"],
            name="fk_speaker_profiles_owner_person",
            ondelete="CASCADE",
        ),
    )
    profile_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    person_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    display_name: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer)
    enrollment_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    origin: Mapped[str] = mapped_column(String(16), default="enrollment", server_default="enrollment")
    source_observation_id: Mapped[str | None] = mapped_column(
        ForeignKey("candidate_voice_observations.observation_id", ondelete="CASCADE"), nullable=True)
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
    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "person_id"],
            ["persons.owner_id", "persons.person_id"],
            name="fk_voice_enrollments_owner_person",
            ondelete="CASCADE",
        ),
    )
    enrollment_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    person_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    profile_id: Mapped[str] = mapped_column(String(64))
    request_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    samples_json: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    state: Mapped[str] = mapped_column(String(16), default="collecting")
    expires_at: Mapped[float] = mapped_column(Float)


class CandidateVoiceObservationRow(Base):
    """Source receipts only; templates/recordings retain their existing owners."""

    __tablename__ = 'candidate_voice_observations'
    observation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64))
    person_id: Mapped[str] = mapped_column(String(64))
    session_id: Mapped[str] = mapped_column(String(128))
    turn_id: Mapped[str] = mapped_column(String(128))
    audio_sha256: Mapped[str] = mapped_column(String(64))
    profile_id: Mapped[str] = mapped_column(String(64))
    profile_revision: Mapped[int] = mapped_column(Integer)
    request_digest: Mapped[str] = mapped_column(String(64))
    observation_json: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (
        ForeignKeyConstraint(['owner_id', 'person_id'], ['persons.owner_id', 'persons.person_id'], ondelete='CASCADE'),
        Index('uq_candidate_voice_turn', 'owner_id', 'session_id', 'turn_id', unique=True),
        Index('uq_candidate_voice_audio', 'owner_id', 'audio_sha256', unique=True),
        Index('ix_candidate_voice_person', 'owner_id', 'person_id'),
    )
