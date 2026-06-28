"""Allow unclaimed devices without an owner."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0002_nullable_device_owner"
down_revision = "0001_core_schema"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("devices") as batch_op:
        batch_op.alter_column(
            "owner_id",
            existing_type=sa.String(length=64),
            nullable=True,
        )


def downgrade() -> None:
    with op.batch_alter_table("devices") as batch_op:
        batch_op.alter_column(
            "owner_id",
            existing_type=sa.String(length=64),
            nullable=False,
        )
