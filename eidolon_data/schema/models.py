"""SQLAlchemy rows for the Eidolon Data core schema."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from eidolon_data.db.base import Base, utc_now

JsonDict = dict[str, Any]


class OwnerRow(Base):
    __tablename__ = "owners"

    owner_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    kind: Mapped[str] = mapped_column(String(32), default="person", index=True)
    profile_json: Mapped[JsonDict] = mapped_column(default=dict)
    settings_json: Mapped[JsonDict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class CompanionRow(Base):
    __tablename__ = "companions"

    companion_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(128), default="")
    kind: Mapped[str] = mapped_column(String(32), default="companion", index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    current_genome_id: Mapped[str | None] = mapped_column(String(64), index=True)
    default_memory_realm_id: Mapped[str | None] = mapped_column(String(64), index=True)
    profile_json: Mapped[JsonDict] = mapped_column(default=dict)
    runtime_config_json: Mapped[JsonDict] = mapped_column(default=dict)
    metadata_json: Mapped[JsonDict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    owner: Mapped[OwnerRow] = relationship()


class PersonaGenomeRow(Base):
    __tablename__ = "persona_genomes"

    genome_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    companion_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companions.companion_id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    source_json: Mapped[JsonDict] = mapped_column(default=dict)
    genome_json: Mapped[JsonDict] = mapped_column(default=dict)
    evolution_state_json: Mapped[JsonDict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("companion_id", "version", name="uq_persona_genomes_companion_version"),
    )


class DeviceRow(Base):
    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(128), default="")
    kind: Mapped[str] = mapped_column(String(64), default="unknown", index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(128))
    bound_companion_id: Mapped[str | None] = mapped_column(String(64), index=True)
    interaction_mode: Mapped[str | None] = mapped_column(String(64))
    auth_type: Mapped[str | None] = mapped_column(String(32))
    secret_ref: Mapped[str | None] = mapped_column(Text)
    capabilities_json: Mapped[JsonDict] = mapped_column(default=dict)
    network_json: Mapped[JsonDict] = mapped_column(default=dict)
    access_policy_json: Mapped[JsonDict] = mapped_column(default=dict)
    metadata_json: Mapped[JsonDict] = mapped_column(default=dict)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ConversationRow(Base):
    __tablename__ = "conversations"

    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    companion_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companions.companion_id", ondelete="CASCADE"), index=True
    )
    device_id: Mapped[str | None] = mapped_column(String(128), index=True)
    title: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[JsonDict] = mapped_column(default=dict)

    __table_args__ = (
        Index("ix_conversations_owner_started", "owner_id", "started_at"),
        Index("ix_conversations_owner_updated", "owner_id", "updated_at"),
    )


class TurnRow(Base):
    __tablename__ = "turns"

    turn_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    conversation_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("conversations.conversation_id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    device_id: Mapped[str | None] = mapped_column(String(128), index=True)
    trigger: Mapped[str] = mapped_column(String(32), default="user")
    status: Mapped[str] = mapped_column(String(32), default="completed", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    trace_json: Mapped[JsonDict] = mapped_column(default=dict)
    metrics_json: Mapped[JsonDict] = mapped_column(default=dict)
    metadata_json: Mapped[JsonDict] = mapped_column(default=dict)

    __table_args__ = (UniqueConstraint("conversation_id", "seq", name="uq_turns_conversation_seq"),)


class MessageRow(Base):
    __tablename__ = "messages"

    message_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    turn_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("turns.turn_id", ondelete="CASCADE"), index=True
    )
    seq: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(32), index=True)
    content: Mapped[str] = mapped_column(Text)
    content_type: Mapped[str] = mapped_column(String(64), default="text/plain")
    visibility: Mapped[str] = mapped_column(String(32), default="normal", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    metadata_json: Mapped[JsonDict] = mapped_column(default=dict)

    __table_args__ = (
        UniqueConstraint("turn_id", "seq", name="uq_messages_turn_seq"),
        Index("ix_messages_turn_created", "turn_id", "created_at"),
    )


class MemoryRealmRow(Base):
    __tablename__ = "memory_realms"

    realm_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    companion_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companions.companion_id", ondelete="CASCADE"), index=True
    )
    engine: Mapped[str] = mapped_column(String(64), default="mempalace", index=True)
    engine_config_json: Mapped[JsonDict] = mapped_column(default=dict)
    policy_json: Mapped[JsonDict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class JobRow(Base):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    companion_id: Mapped[str | None] = mapped_column(String(64), index=True)
    conversation_id: Mapped[str | None] = mapped_column(String(64), index=True)
    turn_id: Mapped[str | None] = mapped_column(String(64), index=True)
    provider: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    input_json: Mapped[JsonDict] = mapped_column(default=dict)
    provider_ref_json: Mapped[JsonDict] = mapped_column(default=dict)
    progress_json: Mapped[JsonDict] = mapped_column(default=dict)
    result_json: Mapped[JsonDict] = mapped_column(default=dict)
    error_json: Mapped[JsonDict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class EventRow(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    subject_type: Mapped[str] = mapped_column(String(32), index=True)
    subject_id: Mapped[str] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(96), index=True)
    actor_type: Mapped[str] = mapped_column(String(32), default="system", index=True)
    actor_id: Mapped[str | None] = mapped_column(String(128), index=True)
    payload_json: Mapped[JsonDict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    __table_args__ = (Index("ix_events_subject_created", "subject_type", "subject_id", "created_at"),)
