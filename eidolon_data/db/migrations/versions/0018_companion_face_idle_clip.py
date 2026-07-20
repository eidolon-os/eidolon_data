"""Add offline idle-clip fields to companion face assets."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0018_companion_face_idle_clip"
down_revision = "0017_companion_face_assets"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("companion_face_assets") as batch:
        batch.add_column(
            sa.Column(
                "idle_status",
                sa.String(length=16),
                nullable=False,
                server_default="none",
            )
        )
        batch.add_column(sa.Column("idle_storage_key", sa.String(length=256), nullable=True))
        batch.add_column(sa.Column("idle_content_type", sa.String(length=32), nullable=True))
        batch.add_column(sa.Column("idle_size_bytes", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("idle_sha256", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("idle_error", sa.Text(), nullable=True))
        batch.create_check_constraint(
            "ck_companion_face_asset_idle_status",
            "idle_status IN ('none', 'pending', 'generating', 'ready', 'failed')",
        )


def downgrade() -> None:
    with op.batch_alter_table("companion_face_assets") as batch:
        batch.drop_constraint("ck_companion_face_asset_idle_status", type_="check")
        batch.drop_column("idle_error")
        batch.drop_column("idle_sha256")
        batch.drop_column("idle_size_bytes")
        batch.drop_column("idle_content_type")
        batch.drop_column("idle_storage_key")
        batch.drop_column("idle_status")
