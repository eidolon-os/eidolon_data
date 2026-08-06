"""Create the clean System Data V2 authority schema.

Revision ID: 0001_system_data_v2
Revises: none
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_system_data_v2"
down_revision = None
branch_labels = None
depends_on = None

NOW = sa.text("CURRENT_TIMESTAMP")
EMPTY_JSON = sa.text("'{}'")


def upgrade() -> None:
    op.create_table(
        "owners",
        sa.Column("owner_id", sa.String(64), primary_key=True),
        sa.Column("display_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("kind", sa.String(32), nullable=False, server_default="person"),
        sa.Column("status", sa.String(32), nullable=False, server_default="active"),
        sa.Column("profile_json", sa.JSON(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("settings_json", sa.JSON(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("kind IN ('person', 'family', 'team')", name="owner_kind"),
        sa.CheckConstraint("status IN ('active', 'archived', 'deleting')", name="owner_status"),
    )
    op.create_index("ix_owners_kind", "owners", ["kind"])
    op.create_index("ix_owners_status", "owners", ["status"])

    op.create_table(
        "companions",
        sa.Column("companion_id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("display_name", sa.String(128), nullable=False, server_default=""),
        sa.Column("role", sa.String(16), nullable=False, server_default="standard"),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("current_genome_id", sa.String(64)),
        sa.Column("default_memory_realm_id", sa.String(64)),
        sa.Column("profile_json", sa.JSON(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("runtime_config_json", sa.JSON(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("metadata_json", sa.JSON(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.owner_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["current_genome_id"],
            ["persona_genomes.genome_id"],
            name="fk_companions_current_genome",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["default_memory_realm_id"],
            ["memory_realms.realm_id"],
            name="fk_companions_default_memory_realm",
            ondelete="SET NULL",
        ),
        sa.UniqueConstraint("owner_id", "companion_id", name="uq_companions_owner_companion"),
        sa.CheckConstraint("role IN ('primary', 'standard', 'guard')", name="companion_role"),
        sa.CheckConstraint("status IN ('active', 'inactive', 'deleting')", name="companion_status"),
    )
    op.create_index("ix_companions_owner_id", "companions", ["owner_id"])
    op.create_index("ix_companions_role", "companions", ["role"])
    op.create_index("ix_companions_status", "companions", ["status"])
    op.create_index("ix_companions_current_genome_id", "companions", ["current_genome_id"])
    op.create_index(
        "ix_companions_default_memory_realm_id",
        "companions",
        ["default_memory_realm_id"],
    )
    op.create_index(
        "uq_companions_owner_primary",
        "companions",
        ["owner_id"],
        unique=True,
        sqlite_where=sa.text("role = 'primary' AND status = 'active'"),
        postgresql_where=sa.text("role = 'primary' AND status = 'active'"),
    )

    op.create_table(
        "persona_genomes",
        sa.Column("genome_id", sa.String(64), primary_key=True),
        sa.Column("companion_id", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="committed"),
        sa.Column("base_genome_id", sa.String(64)),
        sa.Column("schema_version", sa.String(64), nullable=False),
        sa.Column("genome_hash", sa.String(80), nullable=False),
        sa.Column("realizer_version", sa.String(64), nullable=False),
        sa.Column("applied_event_id", sa.String(64)),
        sa.Column("source_json", sa.JSON(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("genome_json", sa.JSON(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("change_summary", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(["companion_id"], ["companions.companion_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["base_genome_id"], ["persona_genomes.genome_id"], ondelete="SET NULL"
        ),
        sa.UniqueConstraint("companion_id", "version", name="uq_persona_genomes_companion_version"),
        sa.CheckConstraint("version > 0", name="persona_genome_version_positive"),
        sa.CheckConstraint(
            "status IN ('proposed', 'committed', 'rejected', 'stale')",
            name="persona_genome_status",
        ),
        sa.CheckConstraint(
            "schema_version = 'eidolon.persona_genome'",
            name="persona_genome_schema_current",
        ),
        sa.CheckConstraint(
            "realizer_version = 'eidolon.persona_realizer'",
            name="persona_genome_realizer_current",
        ),
    )
    for column in (
        "companion_id",
        "status",
        "base_genome_id",
        "schema_version",
        "genome_hash",
        "applied_event_id",
    ):
        op.create_index(f"ix_persona_genomes_{column}", "persona_genomes", [column])

    op.create_table(
        "memory_realms",
        sa.Column("realm_id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("companion_id", sa.String(64), nullable=False),
        sa.Column("engine", sa.String(64), nullable=False, server_default="mempalace"),
        sa.Column("engine_config_json", sa.JSON(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("policy_json", sa.JSON(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.owner_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["companion_id"], ["companions.companion_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_id", "companion_id"],
            ["companions.owner_id", "companions.companion_id"],
            name="fk_memory_realms_owner_companion",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive', 'deleting')", name="memory_realm_status"
        ),
    )
    for column in ("owner_id", "companion_id", "engine", "status"):
        op.create_index(f"ix_memory_realms_{column}", "memory_realms", [column])

    op.create_table(
        "companion_face_assets",
        sa.Column("face_asset_id", sa.String(64), primary_key=True),
        sa.Column("companion_id", sa.String(64), nullable=False),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="active"),
        sa.Column("source", sa.String(32), nullable=False, server_default="upload"),
        sa.Column("cond_storage_key", sa.String(256), nullable=False, unique=True),
        sa.Column("cond_content_type", sa.String(32), nullable=False),
        sa.Column("cond_size_bytes", sa.Integer(), nullable=False),
        sa.Column("cond_sha256", sa.String(64), nullable=False),
        sa.Column("width", sa.Integer()),
        sa.Column("height", sa.Integer()),
        sa.Column("idle_status", sa.String(16), nullable=False, server_default="none"),
        sa.Column("idle_storage_key", sa.String(256), unique=True),
        sa.Column("idle_content_type", sa.String(64)),
        sa.Column("idle_size_bytes", sa.Integer()),
        sa.Column("idle_sha256", sa.String(64)),
        sa.Column("idle_error", sa.Text()),
        sa.Column("meta_json", sa.JSON(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(["companion_id"], ["companions.companion_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.owner_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_id", "companion_id"],
            ["companions.owner_id", "companions.companion_id"],
            name="fk_companion_face_assets_owner_companion",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("companion_id", "version", name="uq_companion_face_asset_version"),
        sa.CheckConstraint("version > 0", name="companion_face_asset_version_positive"),
        sa.CheckConstraint("state IN ('active', 'superseded')", name="companion_face_asset_state"),
        sa.CheckConstraint(
            "cond_content_type = 'image/jpeg'", name="companion_face_asset_content_type"
        ),
        sa.CheckConstraint("cond_size_bytes > 0", name="companion_face_asset_size_positive"),
        sa.CheckConstraint(
            "idle_status IN ('none', 'pending', 'generating', 'ready', 'failed')",
            name="companion_face_asset_idle_status",
        ),
    )
    for column in ("companion_id", "owner_id", "state"):
        op.create_index(f"ix_companion_face_assets_{column}", "companion_face_assets", [column])
    op.create_index(
        "uq_companion_face_asset_active",
        "companion_face_assets",
        ["companion_id"],
        unique=True,
        sqlite_where=sa.text("state = 'active'"),
        postgresql_where=sa.text("state = 'active'"),
    )

    op.create_table(
        "guard_bindings",
        sa.Column("binding_id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("guard_companion_id", sa.String(64), nullable=False),
        sa.Column("device_id", sa.String(128), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="active"),
        sa.Column("policy_id", sa.String(64), nullable=False, server_default="silent_presence"),
        sa.Column("policy_revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("policy_json", sa.JSON(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("disabled_at", sa.DateTime(timezone=True)),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.owner_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_id", "guard_companion_id"],
            ["companions.owner_id", "companions.companion_id"],
            name="fk_guard_bindings_owner_guard_companion",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "state IN ('active', 'disabled', 'revoked')", name="guard_binding_state"
        ),
        sa.CheckConstraint("policy_revision > 0", name="guard_policy_revision_positive"),
    )
    for column in ("owner_id", "guard_companion_id", "device_id", "state"):
        op.create_index(f"ix_guard_bindings_{column}", "guard_bindings", [column])
    op.create_index("ix_guard_bindings_owner_state", "guard_bindings", ["owner_id", "state"])
    for name, column in (
        ("uq_guard_bindings_guard_companion_active", "guard_companion_id"),
        ("uq_guard_bindings_device_active", "device_id"),
    ):
        op.create_index(
            name,
            "guard_bindings",
            [column],
            unique=True,
            sqlite_where=sa.text("state = 'active'"),
            postgresql_where=sa.text("state = 'active'"),
        )

    op.create_table(
        "owner_face_profile_revisions",
        sa.Column("profile_revision_id", sa.String(96), primary_key=True),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="draft"),
        sa.Column("desired_state", sa.String(16), nullable=False, server_default="active"),
        sa.Column("model_id", sa.String(96)),
        sa.Column("preprocessing_version", sa.String(96)),
        sa.Column("activated_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.owner_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("profile_id", "revision", name="uq_owner_face_profile_revision"),
        sa.UniqueConstraint("owner_id", "revision", name="uq_owner_face_owner_revision"),
        sa.CheckConstraint("revision > 0", name="owner_face_profile_revision_positive"),
        sa.CheckConstraint(
            "state IN ('draft', 'desired', 'superseded')", name="owner_face_profile_state"
        ),
        sa.CheckConstraint(
            "desired_state IN ('active', 'cleared')", name="owner_face_profile_desired_state"
        ),
        sa.CheckConstraint(
            "(desired_state = 'active' AND model_id IS NOT NULL AND preprocessing_version IS NOT NULL) OR "
            "(desired_state = 'cleared' AND model_id IS NULL AND preprocessing_version IS NULL)",
            name="owner_face_profile_model_state",
        ),
    )
    for column in ("profile_id", "owner_id", "state"):
        op.create_index(
            f"ix_owner_face_profile_revisions_{column}",
            "owner_face_profile_revisions",
            [column],
        )
    op.create_index(
        "uq_owner_face_profile_owner_desired",
        "owner_face_profile_revisions",
        ["owner_id"],
        unique=True,
        sqlite_where=sa.text("state = 'desired'"),
        postgresql_where=sa.text("state = 'desired'"),
    )

    op.create_table(
        "owner_face_references",
        sa.Column("reference_id", sa.String(96), primary_key=True),
        sa.Column("profile_revision_id", sa.String(96), nullable=False),
        sa.Column("pose", sa.String(16), nullable=False),
        sa.Column("content_type", sa.String(32), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(256), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.ForeignKeyConstraint(
            ["profile_revision_id"],
            ["owner_face_profile_revisions.profile_revision_id"],
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint("profile_revision_id", "pose", name="uq_owner_face_reference_pose"),
        sa.CheckConstraint(
            "pose IN ('front', 'left', 'right', 'down', 'up')",
            name="owner_face_reference_pose",
        ),
        sa.CheckConstraint("content_type = 'image/jpeg'", name="owner_face_reference_content_type"),
        sa.CheckConstraint("size_bytes > 0", name="owner_face_reference_size_positive"),
    )
    op.create_index(
        "ix_owner_face_references_profile_revision_id",
        "owner_face_references",
        ["profile_revision_id"],
    )

    op.create_table(
        "audit_outbox",
        sa.Column("outbox_id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.String(64), nullable=False, unique=True),
        sa.Column("producer", sa.String(64), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("owner_id", sa.String(64)),
        sa.Column("subject_type", sa.String(64), nullable=False),
        sa.Column("subject_id", sa.String(128), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False, server_default="success"),
        sa.Column("severity", sa.String(16), nullable=False, server_default="info"),
        sa.Column("reason", sa.String(256)),
        sa.Column("trace_id", sa.String(64)),
        sa.Column("data_classification", sa.String(16), nullable=False, server_default="safe"),
        sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("payload_json", sa.JSON(), nullable=False, server_default=EMPTY_JSON),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "next_attempt_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW
        ),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.CheckConstraint("category IN ('governance', 'receipt')", name="audit_outbox_category"),
        sa.CheckConstraint(
            "outcome IN ('success', 'failure', 'denied', 'deferred')",
            name="audit_outbox_outcome",
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warn', 'error', 'critical')", name="audit_outbox_severity"
        ),
        sa.CheckConstraint("attempt_count >= 0", name="audit_outbox_attempt_count"),
    )
    op.create_index(
        "ix_audit_outbox_pending",
        "audit_outbox",
        ["published_at", "next_attempt_at", "outbox_id"],
    )


def downgrade() -> None:
    for table in (
        "audit_outbox",
        "owner_face_references",
        "owner_face_profile_revisions",
        "guard_bindings",
        "companion_face_assets",
        "memory_realms",
        "persona_genomes",
        "companions",
        "owners",
    ):
        op.drop_table(table)
