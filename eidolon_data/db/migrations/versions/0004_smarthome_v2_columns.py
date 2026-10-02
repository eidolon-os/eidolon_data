"""Smart-home registry v2 columns: per-device traits and provenance, provider scenes.

Purely additive. Every new column has a default that means what the v1 row
meant: traits ``NULL`` (the type's preset), source ``manual``, no overrides,
not orphaned; a scene without ``provider_ref`` is still a list of actions.

Revision ID: 0004_smarthome_v2_columns
Revises: 0003_smarthome_registry
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_smarthome_v2_columns"
down_revision = "0003_smarthome_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("smarthome_devices") as batch:
        batch.add_column(sa.Column("traits_json", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("limits_json", sa.JSON(), nullable=True))
        batch.add_column(
            sa.Column("source", sa.String(16), nullable=False, server_default="manual")
        )
        batch.add_column(
            sa.Column("overrides_json", sa.JSON(), nullable=False, server_default=sa.text("'[]'"))
        )
        batch.add_column(sa.Column("synced_at_ms", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("orphaned", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    with op.batch_alter_table("smarthome_scenes") as batch:
        batch.add_column(sa.Column("provider", sa.String(128), nullable=True))
        batch.add_column(sa.Column("provider_ref", sa.String(128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("smarthome_scenes") as batch:
        batch.drop_column("provider_ref")
        batch.drop_column("provider")
    with op.batch_alter_table("smarthome_devices") as batch:
        for column in (
            "orphaned",
            "synced_at_ms",
            "overrides_json",
            "source",
            "limits_json",
            "traits_json",
        ):
            batch.drop_column(column)
