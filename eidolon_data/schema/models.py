"""SQLAlchemy rows for the Eidolon Data core schema."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from eidolon_data.db.base import Base, utc_now

JsonDict = dict[str, Any]


class OwnerRow(Base):
    __tablename__ = "owners"

    owner_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    kind: Mapped[str] = mapped_column(String(32), default="person", index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
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
    # The owner's primary companion. Master companions default-get a local web
    # body; any companion (master or not) can associate more bodies on demand.
    is_master: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    companion_type: Mapped[str] = mapped_column(String(16), default="slave", nullable=False, index=True)
    current_genome_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey(
            "persona_genomes.genome_id",
            name="fk_companions_current_genome",
            ondelete="SET NULL",
            use_alter=True,
        ),
        index=True,
    )
    default_memory_realm_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey(
            "memory_realms.realm_id",
            name="fk_companions_default_memory_realm",
            ondelete="SET NULL",
            use_alter=True,
        ),
        index=True,
    )
    profile_json: Mapped[JsonDict] = mapped_column(default=dict)
    runtime_config_json: Mapped[JsonDict] = mapped_column(default=dict)
    metadata_json: Mapped[JsonDict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    owner: Mapped[OwnerRow] = relationship()

    __table_args__ = (
        UniqueConstraint("owner_id", "companion_id", name="uq_companions_owner_companion"),
    )


class PersonaGenomeRow(Base):
    __tablename__ = "persona_genomes"

    genome_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    companion_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companions.companion_id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(32), default="committed", index=True)
    base_genome_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("persona_genomes.genome_id", ondelete="SET NULL"), index=True
    )
    schema_version: Mapped[str] = mapped_column(String(64), default="eidolon.persona_genome", index=True)
    genome_hash: Mapped[str] = mapped_column(String(80), index=True)
    realizer_version: Mapped[str] = mapped_column(String(64), default="eidolon.persona_realizer")
    applied_event_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source_json: Mapped[JsonDict] = mapped_column(default=dict)
    genome_json: Mapped[JsonDict] = mapped_column(default=dict)
    change_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("companion_id", "version", name="uq_persona_genomes_companion_version"),
        CheckConstraint(
            "schema_version = 'eidolon.persona_genome'",
            name="schema_current",
        ),
        CheckConstraint(
            "realizer_version = 'eidolon.persona_realizer'",
            name="realizer_current",
        ),
    )


class DeviceRow(Base):
    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), nullable=True, index=True
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

    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "bound_companion_id"],
            ["companions.owner_id", "companions.companion_id"],
            name="fk_devices_owner_bound_companion",
        ),
        UniqueConstraint("owner_id", "device_id", name="uq_devices_owner_device"),
    )


class GuardBindingRow(Base):
    """Owner-scoped control-plane binding for a physical Guard device."""

    __tablename__ = "guard_bindings"

    binding_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    guard_companion_id: Mapped[str] = mapped_column(String(64), index=True)
    device_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("devices.device_id", ondelete="RESTRICT"), index=True
    )
    state: Mapped[str] = mapped_column(String(32), default="active", index=True)
    policy_id: Mapped[str] = mapped_column(String(64), default="silent_presence")
    config_revision: Mapped[int] = mapped_column(Integer, default=1)
    config_json: Mapped[JsonDict] = mapped_column(default=dict)
    runtime_revision: Mapped[int] = mapped_column(Integer, default=1)
    runtime_config_json: Mapped[JsonDict] = mapped_column(default=dict)
    desired_runtime_state: Mapped[str] = mapped_column(String(16), default="stopped")
    status_json: Mapped[JsonDict] = mapped_column(default=dict)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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
        Index(
            "uq_guard_bindings_owner_active",
            "owner_id",
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


class GuardPolicyActionRow(Base):
    """Durable policy-action outbox for Guard subscribers."""

    __tablename__ = "guard_policy_actions"

    action_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    binding_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("guard_bindings.binding_id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    guard_companion_id: Mapped[str] = mapped_column(String(64), index=True)
    device_id: Mapped[str] = mapped_column(String(128), index=True)
    correlation_id: Mapped[str] = mapped_column(String(96), index=True)
    guard_epoch: Mapped[int] = mapped_column(Integer)
    fact_type: Mapped[str] = mapped_column(String(64), default="")
    replay_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    policy_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(96))
    subscriber: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(32), default="published", index=True)
    payload_json: Mapped[JsonDict] = mapped_column(default=dict)
    ack_json: Mapped[JsonDict | None] = mapped_column(JSON, default=None)
    command_id: Mapped[str | None] = mapped_column(String(96), index=True)
    delivery_attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    delivery_claim_token: Mapped[str | None] = mapped_column(String(64), nullable=True)
    delivery_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    delivery_dead_lettered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        Index("ix_guard_policy_actions_owner_status", "owner_id", "status"),
        Index(
            "ix_guard_policy_actions_binding_fact_replay",
            "binding_id",
            "correlation_id",
            "guard_epoch",
            "fact_type",
        ),
        Index("uq_guard_policy_actions_replay_key", "replay_key", unique=True),
    )


class GuardRuntimeDeliveryRow(Base):
    """Durable desired-state delivery for one GuardBinding runtime revision.

    This is intentionally separate from ``guard_policy_actions``: policy
    actions are consumed by subscribers, while runtime deliveries reconcile a
    Guard device with its binding-local desired state.
    """

    __tablename__ = "guard_runtime_deliveries"

    delivery_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    binding_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("guard_bindings.binding_id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    device_id: Mapped[str] = mapped_column(String(128), index=True)
    runtime_revision: Mapped[int] = mapped_column(Integer)
    desired_runtime_state: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    command_id: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_json: Mapped[JsonDict | None] = mapped_column(JSON, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("binding_id", "runtime_revision", name="uq_guard_runtime_delivery_revision"),
        Index("ix_guard_runtime_deliveries_device_status", "device_id", "status"),
        Index("ix_guard_runtime_deliveries_binding_created", "binding_id", "created_at"),
    )


class BodyCommandRow(Base):
    __tablename__ = "body_commands"

    command_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="SET NULL"), nullable=True, index=True
    )
    companion_id: Mapped[str | None] = mapped_column(String(64), index=True)
    runtime_caller_id: Mapped[str | None] = mapped_column(
        String(96), ForeignKey("runtime_callers.caller_id", ondelete="SET NULL"), index=True
    )
    runtime_session_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("runtime_sessions.session_id", ondelete="SET NULL"), index=True
    )
    device_id: Mapped[str] = mapped_column(String(128), index=True)
    source_device_id: Mapped[str | None] = mapped_column(String(128), index=True)
    topic: Mapped[str] = mapped_column(String(128), default="", index=True)
    op: Mapped[str] = mapped_column(String(128), default="", index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    payload_json: Mapped[JsonDict] = mapped_column(default=dict)
    envelope_json: Mapped[JsonDict] = mapped_column(default=dict)
    ack_json: Mapped[JsonDict | None] = mapped_column(JSON, default=None)
    result_json: Mapped[JsonDict | None] = mapped_column(JSON, default=None)
    ttl_ms: Mapped[int] = mapped_column(Integer, default=30_000)
    qos: Mapped[str] = mapped_column(String(32), default="ack")
    priority: Mapped[str] = mapped_column(String(32), default="normal")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (
        Index("ix_body_commands_device_created", "device_id", "created_at"),
        Index("ix_body_commands_owner_created", "owner_id", "created_at"),
    )


class RuntimeCallerRow(Base):
    __tablename__ = "runtime_callers"

    caller_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    companion_id: Mapped[str | None] = mapped_column(String(64), index=True)
    actor_kind: Mapped[str] = mapped_column(String(64), index=True)
    actor_id: Mapped[str] = mapped_column(String(128), index=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    source_device_id: Mapped[str | None] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    metadata_json: Mapped[JsonDict] = mapped_column(default=dict)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint(
            "owner_id",
            "actor_kind",
            "actor_id",
            "companion_id",
            name="uq_runtime_callers_identity",
        ),
        ForeignKeyConstraint(
            ["owner_id", "companion_id"],
            ["companions.owner_id", "companions.companion_id"],
            name="fk_runtime_callers_owner_companion",
        ),
    )


class RuntimeSessionRow(Base):
    __tablename__ = "runtime_sessions"

    session_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    runtime_caller_id: Mapped[str | None] = mapped_column(
        String(96), ForeignKey("runtime_callers.caller_id", ondelete="SET NULL"), index=True
    )
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    companion_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source_device_id: Mapped[str | None] = mapped_column(String(128), index=True)
    transport: Mapped[str] = mapped_column(String(64), default="", index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    metadata_json: Mapped[JsonDict] = mapped_column(default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "companion_id"],
            ["companions.owner_id", "companions.companion_id"],
            name="fk_runtime_sessions_owner_companion",
        ),
        Index("ix_runtime_sessions_owner_last_seen", "owner_id", "last_seen_at"),
    )


class ConversationRow(Base):
    __tablename__ = "conversations"

    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    companion_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companions.companion_id", ondelete="CASCADE"), index=True
    )
    runtime_caller_id: Mapped[str | None] = mapped_column(
        String(96), ForeignKey("runtime_callers.caller_id", ondelete="SET NULL"), index=True
    )
    runtime_session_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("runtime_sessions.session_id", ondelete="SET NULL"), index=True
    )
    source_device_id: Mapped[str | None] = mapped_column(String(128), index=True)
    title: Mapped[str | None] = mapped_column(String(256))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[JsonDict] = mapped_column(default=dict)

    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "companion_id"],
            ["companions.owner_id", "companions.companion_id"],
            name="fk_conversations_owner_companion",
            ondelete="CASCADE",
        ),
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
    runtime_caller_id: Mapped[str | None] = mapped_column(
        String(96), ForeignKey("runtime_callers.caller_id", ondelete="SET NULL"), index=True
    )
    runtime_session_id: Mapped[str | None] = mapped_column(
        String(128), ForeignKey("runtime_sessions.session_id", ondelete="SET NULL"), index=True
    )
    source_device_id: Mapped[str | None] = mapped_column(String(128), index=True)
    trigger: Mapped[str] = mapped_column(String(32), default="user")
    status: Mapped[str] = mapped_column(String(32), default="completed", index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Cross-hop correlation id (channel->agent->memory), indexed for querying a
    # turn by trace. The full trace also lives in trace_json; this promotes the
    # id to a first-class filterable column.
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
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

    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "companion_id"],
            ["companions.owner_id", "companions.companion_id"],
            name="fk_memory_realms_owner_companion",
            ondelete="CASCADE",
        ),
    )


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

    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "companion_id"],
            ["companions.owner_id", "companions.companion_id"],
            name="fk_jobs_owner_companion",
        ),
    )


class EventRow(Base):
    __tablename__ = "events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    companion_id: Mapped[str | None] = mapped_column(String(64), index=True)
    subject_type: Mapped[str] = mapped_column(String(32), index=True)
    subject_id: Mapped[str] = mapped_column(String(128), index=True)
    event_type: Mapped[str] = mapped_column(String(96), index=True)
    # Classification (see eidolon_data.events.registry): tier + subsystem + result.
    event_class: Mapped[str] = mapped_column(String(8), default="audit", index=True)
    source: Mapped[str] = mapped_column(String(16), default="data", index=True)
    severity: Mapped[str] = mapped_column(String(8), default="info", index=True)
    outcome: Mapped[str] = mapped_column(String(12), default="success", index=True)
    reason: Mapped[str | None] = mapped_column(String(256))
    actor_type: Mapped[str] = mapped_column(String(32), default="system", index=True)
    actor_id: Mapped[str | None] = mapped_column(String(128), index=True)
    # Correlation. trace_id is constant across a distributed operation.
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    data_classification: Mapped[str] = mapped_column(String(10), default="safe")
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    payload_json: Mapped[JsonDict] = mapped_column(default=dict)
    # occurred_at = when the real-world event happened; created_at = when the row was recorded.
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=utc_now)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now, index=True)

    __table_args__ = (
        Index("ix_events_subject_created", "subject_type", "subject_id", "created_at"),
        Index("ix_events_owner_created", "owner_id", "created_at"),
    )
