"""Make Guard action publication and body delivery concurrency-safe."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0013_guard_action_delivery_leases"
down_revision = "0012_guard_action_delivery_mapping"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "guard_policy_actions",
        sa.Column("delivery_claim_token", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "guard_policy_actions",
        sa.Column("delivery_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "guard_policy_actions",
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "guard_policy_actions",
        sa.Column("delivery_dead_lettered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "guard_policy_actions",
        sa.Column("replay_key", sa.String(length=128), nullable=True),
    )
    op.create_index(
        "ix_guard_policy_actions_delivery_lease_expires_at",
        "guard_policy_actions",
        ["delivery_lease_expires_at"],
    )
    op.create_index(
        "ix_guard_policy_actions_next_attempt_at",
        "guard_policy_actions",
        ["next_attempt_at"],
    )
    op.create_index(
        "ix_guard_policy_actions_delivery_dead_lettered_at",
        "guard_policy_actions",
        ["delivery_dead_lettered_at"],
    )
    op.create_index(
        "uq_guard_policy_actions_replay_key",
        "guard_policy_actions",
        ["replay_key"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_guard_policy_actions_replay_key", table_name="guard_policy_actions")
    op.drop_index(
        "ix_guard_policy_actions_delivery_dead_lettered_at",
        table_name="guard_policy_actions",
    )
    op.drop_index("ix_guard_policy_actions_next_attempt_at", table_name="guard_policy_actions")
    op.drop_index(
        "ix_guard_policy_actions_delivery_lease_expires_at",
        table_name="guard_policy_actions",
    )
    op.drop_column("guard_policy_actions", "next_attempt_at")
    op.drop_column("guard_policy_actions", "delivery_dead_lettered_at")
    op.drop_column("guard_policy_actions", "replay_key")
    op.drop_column("guard_policy_actions", "delivery_lease_expires_at")
    op.drop_column("guard_policy_actions", "delivery_claim_token")
