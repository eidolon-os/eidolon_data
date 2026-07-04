"""Add companions.is_master (owner's primary companion)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_companion_is_master"
down_revision = "0004_runtime_context_body_commands"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("companions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "is_master",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch_op.create_index("ix_companions_is_master", ["is_master"])


def downgrade() -> None:
    with op.batch_alter_table("companions") as batch_op:
        batch_op.drop_index("ix_companions_is_master")
        batch_op.drop_column("is_master")
