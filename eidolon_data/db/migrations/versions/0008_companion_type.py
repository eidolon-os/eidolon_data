"""Add companions.companion_type for master/slave role marking."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0008_companion_type"
down_revision = "0007_event_classification"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("companions") as batch_op:
        batch_op.add_column(
            sa.Column(
                "companion_type",
                sa.String(length=16),
                nullable=False,
                server_default="slave",
            )
        )
        batch_op.create_index("ix_companions_companion_type", ["companion_type"])

    op.execute(
        "UPDATE companions SET companion_type = "
        "CASE WHEN is_master = 1 THEN 'master' ELSE 'slave' END"
    )


def downgrade() -> None:
    with op.batch_alter_table("companions") as batch_op:
        batch_op.drop_index("ix_companions_companion_type")
        batch_op.drop_column("companion_type")
