"""Add turns.trace_id (indexed cross-hop correlation id)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0006_turn_trace_id"
down_revision = "0005_companion_is_master"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("turns") as batch_op:
        batch_op.add_column(sa.Column("trace_id", sa.String(length=64), nullable=True))
        batch_op.create_index("ix_turns_trace_id", ["trace_id"])


def downgrade() -> None:
    with op.batch_alter_table("turns") as batch_op:
        batch_op.drop_index("ix_turns_trace_id")
        batch_op.drop_column("trace_id")
