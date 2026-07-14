"""Add normalized Owner Face Profile desired-state storage."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0014_owner_face_profiles"
down_revision = "0013_guard_action_delivery_leases"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owner_face_profile_revisions",
        sa.Column("profile_revision_id", sa.String(length=96), nullable=False),
        sa.Column("profile_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("desired_state", sa.String(length=16), nullable=False),
        sa.Column("model_id", sa.String(length=96), nullable=True),
        sa.Column("preprocessing_version", sa.String(length=96), nullable=True),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision > 0", name="ck_owner_face_profile_revision_positive"),
        sa.CheckConstraint(
            "state IN ('draft', 'desired', 'superseded')",
            name="ck_owner_face_profile_state",
        ),
        sa.CheckConstraint(
            "desired_state IN ('active', 'cleared')",
            name="ck_owner_face_profile_desired_state",
        ),
        sa.CheckConstraint(
            "(desired_state = 'active' AND model_id IS NOT NULL "
            "AND preprocessing_version IS NOT NULL) OR "
            "(desired_state = 'cleared' AND model_id IS NULL "
            "AND preprocessing_version IS NULL)",
            name="ck_owner_face_profile_model_state",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.owner_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("profile_revision_id"),
        sa.UniqueConstraint(
            "profile_id", "revision", name="uq_owner_face_profile_revision"
        ),
        sa.UniqueConstraint(
            "owner_id", "revision", name="uq_owner_face_owner_revision"
        ),
    )
    op.create_index(
        "ix_owner_face_profile_revisions_state",
        "owner_face_profile_revisions",
        ["state"],
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
        sa.Column("reference_id", sa.String(length=96), nullable=False),
        sa.Column("profile_revision_id", sa.String(length=96), nullable=False),
        sa.Column("pose", sa.String(length=16), nullable=False),
        sa.Column("content_type", sa.String(length=32), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "pose IN ('front', 'left', 'right', 'down', 'up')",
            name="ck_owner_face_reference_pose",
        ),
        sa.CheckConstraint(
            "content_type = 'image/jpeg'",
            name="ck_owner_face_reference_content_type",
        ),
        sa.CheckConstraint(
            "size_bytes > 0", name="ck_owner_face_reference_size_positive"
        ),
        sa.ForeignKeyConstraint(
            ["profile_revision_id"],
            ["owner_face_profile_revisions.profile_revision_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("reference_id"),
        sa.UniqueConstraint(
            "profile_revision_id", "pose", name="uq_owner_face_reference_pose"
        ),
        sa.UniqueConstraint("storage_key"),
    )

    op.create_table(
        "guard_owner_face_profile_deliveries",
        sa.Column("delivery_id", sa.String(length=96), nullable=False),
        sa.Column("binding_id", sa.String(length=64), nullable=False),
        sa.Column("profile_revision_id", sa.String(length=96), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("command_id", sa.String(length=64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('pending', 'dispatching', 'dispatched', 'applied', "
            "'failed', 'superseded')",
            name="ck_guard_owner_face_profile_delivery_status",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0", name="ck_guard_owner_face_attempt_count"
        ),
        sa.ForeignKeyConstraint(
            ["binding_id"], ["guard_bindings.binding_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["profile_revision_id"],
            ["owner_face_profile_revisions.profile_revision_id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("delivery_id"),
        sa.UniqueConstraint("command_id"),
        sa.UniqueConstraint(
            "binding_id",
            "profile_revision_id",
            name="uq_guard_owner_face_profile_delivery_revision",
        ),
    )
    op.create_index(
        "ix_guard_owner_face_profile_deliveries_status",
        "guard_owner_face_profile_deliveries",
        ["status"],
    )
    op.create_index(
        "ix_guard_owner_face_profile_deliveries_lease_expires_at",
        "guard_owner_face_profile_deliveries",
        ["lease_expires_at"],
    )
    op.create_index(
        "ix_guard_owner_face_profile_deliveries_binding_created",
        "guard_owner_face_profile_deliveries",
        ["binding_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_table("guard_owner_face_profile_deliveries")
    op.drop_table("owner_face_references")
    op.drop_table("owner_face_profile_revisions")
