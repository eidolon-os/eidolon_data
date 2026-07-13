"""Track Guard action replay keys and body command delivery mapping."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0012_guard_action_delivery_mapping"
down_revision = "0011_guard_runtime_delivery"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "guard_policy_actions",
        sa.Column("fact_type", sa.String(length=64), nullable=False, server_default=""),
    )
    op.add_column("guard_policy_actions", sa.Column("command_id", sa.String(length=96), nullable=True))
    op.add_column(
        "guard_policy_actions",
        sa.Column("delivery_attempt_count", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "guard_policy_actions",
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
    )
    op.add_column(
        "guard_policy_actions",
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_guard_policy_actions_command_id", "guard_policy_actions", ["command_id"])
    op.create_index(
        "ix_guard_policy_actions_binding_fact_replay",
        "guard_policy_actions",
        ["binding_id", "correlation_id", "guard_epoch", "fact_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_guard_policy_actions_binding_fact_replay", table_name="guard_policy_actions")
    op.drop_index("ix_guard_policy_actions_command_id", table_name="guard_policy_actions")
    op.drop_column("guard_policy_actions", "dispatched_at")
    op.drop_column("guard_policy_actions", "last_error")
    op.drop_column("guard_policy_actions", "delivery_attempt_count")
    op.drop_column("guard_policy_actions", "command_id")
    op.drop_column("guard_policy_actions", "fact_type")
