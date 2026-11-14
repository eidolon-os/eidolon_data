"""Data owns Person voiceprint evidence, never inference or conversation admission."""

from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
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
from sqlalchemy.sql import false

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


class VoiceAttributionRow(Base):
    """Data's record that the voice heard in some turns is a Person's (design §3.5).

    Every change takes the Owner's next ``change_seq``, so readers of the
    attribution feed see each attribution's latest state in order.
    """

    __tablename__ = "voice_attributions"
    __table_args__ = (
        CheckConstraint("source IN ('turn','accumulated','owner')", name="voice_attribution_source"),
        Index("uq_voice_attribution_change", "owner_id", "change_seq", unique=True),
        Index("ix_voice_attribution_person", "owner_id", "person_id"),
    )
    attribution_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(ForeignKey("owners.owner_id", ondelete="CASCADE"))
    # Not a foreign key: a deleted Person's attributions stay, revoked, so the
    # feed can carry the revocation.
    person_id: Mapped[str] = mapped_column(String(64))
    person_revision: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(String(16))
    refs_json: Mapped[list] = mapped_column(JSON)
    verdict_digest: Mapped[str] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(Integer)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    recorded_at: Mapped[float] = mapped_column(Float)
    change_seq: Mapped[int] = mapped_column(Integer)


class VoiceAttributionRefRow(Base):
    """One turn's voice belongs to at most one attribution."""

    __tablename__ = "voice_attribution_refs"
    owner_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    turn_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    attribution_id: Mapped[str] = mapped_column(
        ForeignKey("voice_attributions.attribution_id", ondelete="CASCADE"), index=True)


class VoiceObservationTemplateRow(Base):
    """A Person's observation template on one capture channel, from one attributed track.

    At most MAX_CHANNEL_TEMPLATES per Person and channel, the latest kept
    (design §3.2). Withdrawn with the attribution that made it.
    """

    __tablename__ = "voice_observation_templates"
    __table_args__ = (
        ForeignKeyConstraint(["owner_id", "person_id"], ["persons.owner_id", "persons.person_id"],
                             name="fk_voice_observation_templates_owner_person", ondelete="CASCADE"),
        Index("uq_voice_observation_template_track", "owner_id", "person_id", "channel",
              "session_id", "track_id", unique=True),
        Index("ix_voice_observation_template_channel", "owner_id", "person_id", "channel"),
    )
    template_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64))
    person_id: Mapped[str] = mapped_column(String(64))
    channel: Mapped[str] = mapped_column(String(96))
    session_id: Mapped[str] = mapped_column(String(128))
    track_id: Mapped[str] = mapped_column(String(64))
    attribution_id: Mapped[str] = mapped_column(
        ForeignKey("voice_attributions.attribution_id", ondelete="CASCADE"), index=True)
    revision: Mapped[int] = mapped_column(Integer)
    sample_json: Mapped[dict[str, Any]] = mapped_column(JSON)
    updated_at: Mapped[float] = mapped_column(Float)
