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
    companion_type: Mapped[str] = mapped_column(
        String(16), default="slave", nullable=False, index=True
    )
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
    schema_version: Mapped[str] = mapped_column(
        String(64), default="eidolon.persona_genome", index=True
    )
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


class CompanionFaceAssetRow(Base):
    """A versioned display-face asset (the digital-human ``cond_image``) for one companion.

    The avatar worker seeds the digital-human service with a per-companion still
    image; this row is the sovereign, versioned pointer to that image's bytes in
    the object store (SQL holds only the opaque ``cond_storage_key`` + integrity
    metadata, never the bytes — same split as :class:`OwnerFaceReferenceRow`).

    A display face is deliberately *not* part of the semantic persona genome,
    which is model/appearance-independent: swapping the photo is not a persona
    evolution and must not bump the genome hash.  At most one row per companion
    is ``active``; earlier versions become ``superseded`` (mirrors the Owner Face
    Profile revision-state pattern).  ``cond_sha256`` doubles as the source hash
    used to detect a changed image (e.g. to invalidate downstream idle clips).
    """

    __tablename__ = "companion_face_assets"

    face_asset_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    companion_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companions.companion_id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer, default=1)
    state: Mapped[str] = mapped_column(String(16), default="active", index=True)
    source: Mapped[str] = mapped_column(String(16), default="upload")
    cond_storage_key: Mapped[str] = mapped_column(String(256), unique=True)
    cond_content_type: Mapped[str] = mapped_column(String(32))
    cond_size_bytes: Mapped[int] = mapped_column(Integer)
    cond_sha256: Mapped[str] = mapped_column(String(64))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    # Offline-generated photoreal idle loop (plan §8.1/§8.2), keyed to this face
    # version so a new upload starts fresh. ``idle_status`` drives the generation
    # lifecycle: none → pending → generating → ready | failed.
    idle_status: Mapped[str] = mapped_column(
        String(16), default="none", server_default="none"
    )
    idle_storage_key: Mapped[str | None] = mapped_column(String(256))
    idle_content_type: Mapped[str | None] = mapped_column(String(32))
    idle_size_bytes: Mapped[int | None] = mapped_column(Integer)
    idle_sha256: Mapped[str | None] = mapped_column(String(64))
    idle_error: Mapped[str | None] = mapped_column(Text)
    # Forward-compatible bag for secondary refs the semantic genome must not carry
    # (e.g. a still ``poster_ref``; see the avatar integration plan §8.1).
    meta_json: Mapped[JsonDict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("companion_id", "version", name="uq_companion_face_asset_version"),
        Index(
            "uq_companion_face_asset_active",
            "companion_id",
            unique=True,
            sqlite_where=text("state = 'active'"),
            postgresql_where=text("state = 'active'"),
        ),
        CheckConstraint("version > 0", name="ck_companion_face_asset_version_positive"),
        CheckConstraint(
            "state IN ('active', 'superseded')",
            name="ck_companion_face_asset_state",
        ),
        CheckConstraint(
            "cond_content_type = 'image/jpeg'",
            name="ck_companion_face_asset_content_type",
        ),
        CheckConstraint("cond_size_bytes > 0", name="ck_companion_face_asset_size_positive"),
        CheckConstraint(
            "idle_status IN ('none', 'pending', 'generating', 'ready', 'failed')",
            name="ck_companion_face_asset_idle_status",
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
        UniqueConstraint(
            "owner_id",
            "device_id",
            name="uq_guard_bindings_owner_device",
        ),
        Index(
            "uq_guard_bindings_guard_companion_active",
            "guard_companion_id",
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
        UniqueConstraint(
            "binding_id", "runtime_revision", name="uq_guard_runtime_delivery_revision"
        ),
        Index("ix_guard_runtime_deliveries_device_status", "device_id", "status"),
        Index("ix_guard_runtime_deliveries_binding_created", "binding_id", "created_at"),
    )


class OwnerFaceProfileRevisionRow(Base):
    """One immutable-on-activation Owner Face Profile revision."""

    __tablename__ = "owner_face_profile_revisions"

    profile_revision_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(64))
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE")
    )
    revision: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(32), default="draft", index=True)
    desired_state: Mapped[str] = mapped_column(String(16), default="active")
    model_id: Mapped[str | None] = mapped_column(String(96))
    preprocessing_version: Mapped[str | None] = mapped_column(String(96))
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("profile_id", "revision", name="uq_owner_face_profile_revision"),
        UniqueConstraint("owner_id", "revision", name="uq_owner_face_owner_revision"),
        Index(
            "uq_owner_face_profile_owner_desired",
            "owner_id",
            unique=True,
            sqlite_where=text("state = 'desired'"),
            postgresql_where=text("state = 'desired'"),
        ),
        CheckConstraint("revision > 0", name="ck_owner_face_profile_revision_positive"),
        CheckConstraint(
            "state IN ('draft', 'desired', 'superseded')",
            name="ck_owner_face_profile_state",
        ),
        CheckConstraint(
            "desired_state IN ('active', 'cleared')",
            name="ck_owner_face_profile_desired_state",
        ),
        CheckConstraint(
            "(desired_state = 'active' AND model_id IS NOT NULL "
            "AND preprocessing_version IS NOT NULL) OR "
            "(desired_state = 'cleared' AND model_id IS NULL "
            "AND preprocessing_version IS NULL)",
            name="ck_owner_face_profile_model_state",
        ),
    )


class OwnerFaceReferenceRow(Base):
    """Pose-labelled reference linked to normalized object metadata."""

    __tablename__ = "owner_face_references"

    reference_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    profile_revision_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("owner_face_profile_revisions.profile_revision_id", ondelete="CASCADE"),
    )
    pose: Mapped[str] = mapped_column(String(16))
    content_type: Mapped[str] = mapped_column(String(32))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(256), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("profile_revision_id", "pose", name="uq_owner_face_reference_pose"),
        CheckConstraint(
            "pose IN ('front', 'left', 'right', 'down', 'up')",
            name="ck_owner_face_reference_pose",
        ),
        CheckConstraint(
            "content_type = 'image/jpeg'",
            name="ck_owner_face_reference_content_type",
        ),
        CheckConstraint("size_bytes > 0", name="ck_owner_face_reference_size_positive"),
    )


class GuardOwnerFaceProfileDeliveryRow(Base):
    """Durable convergence of one profile revision to one Guard binding."""

    __tablename__ = "guard_owner_face_profile_deliveries"

    delivery_id: Mapped[str] = mapped_column(String(96), primary_key=True)
    binding_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("guard_bindings.binding_id", ondelete="CASCADE")
    )
    profile_revision_id: Mapped[str] = mapped_column(
        String(96),
        ForeignKey("owner_face_profile_revisions.profile_revision_id", ondelete="CASCADE"),
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    command_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str] = mapped_column(Text, default="")
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint(
            "binding_id",
            "profile_revision_id",
            name="uq_guard_owner_face_profile_delivery_revision",
        ),
        CheckConstraint(
            "status IN ('pending', 'dispatching', 'dispatched', 'applied', 'failed', 'superseded')",
            name="ck_guard_owner_face_profile_delivery_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_guard_owner_face_attempt_count"),
        Index(
            "ix_guard_owner_face_profile_deliveries_binding_created",
            "binding_id",
            "created_at",
        ),
    )


class BodyCommandRow(Base):
    __tablename__ = "body_commands"

    command_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="SET NULL"), nullable=True, index=True
    )
    companion_id: Mapped[str | None] = mapped_column(String(64), index=True)
    runtime_session_id: Mapped[str | None] = mapped_column(
        String(128), index=True
    )
    device_id: Mapped[str] = mapped_column(String(128), index=True)
    source_device_id: Mapped[str | None] = mapped_column(String(128), index=True)
    topic: Mapped[str] = mapped_column(String(128), default="", index=True)
    op: Mapped[str] = mapped_column(String(128), default="", index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    payload_json: Mapped[JsonDict] = mapped_column(default=dict)
    envelope_json: Mapped[JsonDict] = mapped_column(default=dict)
    ack_json: Mapped[JsonDict | None] = mapped_column(JSON, default=None)
    result_json: Mapped[Any | None] = mapped_column(JSON, default=None)
    ttl_ms: Mapped[int] = mapped_column(Integer, default=30_000)
    qos: Mapped[str] = mapped_column(String(32), default="ack")
    priority: Mapped[str] = mapped_column(String(32), default="normal")
    error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, index=True
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (
        Index("ix_body_commands_device_created", "device_id", "created_at"),
        Index("ix_body_commands_owner_created", "owner_id", "created_at"),
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


class AuditOutboxRow(Base):
    """Minimal durable hand-off from a Data transaction to the audit plane.

    This table is not the global audit ledger and is never queried by Mission
    Control. Rows only live here until a transport adapter durably acknowledges
    the event. ``outbox_id`` is the producer-local sequence; global total order
    is deliberately not promised.
    """

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
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    last_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        CheckConstraint(
            "category IN ('governance', 'receipt')",
            name="ck_audit_outbox_category",
        ),
        CheckConstraint(
            "outcome IN ('success', 'failure', 'denied', 'deferred')",
            name="ck_audit_outbox_outcome",
        ),
        CheckConstraint(
            "severity IN ('info', 'warn', 'error', 'critical')",
            name="ck_audit_outbox_severity",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_audit_outbox_attempt_count"),
        Index(
            "ix_audit_outbox_pending",
            "published_at",
            "next_attempt_at",
            "outbox_id",
        ),
    )

    # Compatibility properties for the Data-local event facade. They are not
    # persisted twice and do not reintroduce a second event authority.
    @property
    def event_type(self) -> str:
        return self.action

    @property
    def event_class(self) -> str:
        return "audit"

    @property
    def source(self) -> str:
        from eidolon_data.events.registry import spec_for

        spec = spec_for(self.action)
        return spec.source if spec is not None else "data"

    @property
    def companion_id(self) -> str | None:
        value = (self.payload_json or {}).get("companion_id")
        return str(value) if value else None
