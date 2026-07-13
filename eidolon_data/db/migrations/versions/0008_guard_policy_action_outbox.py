"""Add durable Guard policy-action outbox."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_guard_policy_action_outbox"
down_revision = "0007_guard_control_plane"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guard_policy_actions",
        sa.Column("action_id", sa.String(length=96), nullable=False),
        sa.Column("binding_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("guard_companion_id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("correlation_id", sa.String(length=96), nullable=False),
        sa.Column("guard_epoch", sa.Integer(), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=False),
        sa.Column("action", sa.String(length=96), nullable=False),
        sa.Column("subscriber", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("ack_json", sa.JSON(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["binding_id"], ["guard_bindings.binding_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.owner_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("action_id"),
    )
    op.create_index("ix_guard_policy_actions_binding_id", "guard_policy_actions", ["binding_id"])
    op.create_index("ix_guard_policy_actions_owner_id", "guard_policy_actions", ["owner_id"])
    op.create_index("ix_guard_policy_actions_guard_companion_id", "guard_policy_actions", ["guard_companion_id"])
    op.create_index("ix_guard_policy_actions_device_id", "guard_policy_actions", ["device_id"])
    op.create_index("ix_guard_policy_actions_correlation_id", "guard_policy_actions", ["correlation_id"])
    op.create_index("ix_guard_policy_actions_status", "guard_policy_actions", ["status"])
    op.create_index("ix_guard_policy_actions_owner_status", "guard_policy_actions", ["owner_id", "status"])
    op.create_index("ix_guard_policy_actions_binding_correlation", "guard_policy_actions", ["binding_id", "correlation_id"])


def downgrade() -> None:
    op.drop_table("guard_policy_actions")
