"""Add versioned companion display-face (digital-human cond_image) assets."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0017_companion_face_assets"
down_revision = "0016_multi_guard_companion_workspace"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "companion_face_assets",
        sa.Column("face_asset_id", sa.String(length=64), nullable=False),
        sa.Column("companion_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("cond_storage_key", sa.String(length=256), nullable=False),
        sa.Column("cond_content_type", sa.String(length=32), nullable=False),
        sa.Column("cond_size_bytes", sa.Integer(), nullable=False),
        sa.Column("cond_sha256", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("meta_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version > 0", name="ck_companion_face_asset_version_positive"),
        sa.CheckConstraint(
            "state IN ('active', 'superseded')",
            name="ck_companion_face_asset_state",
        ),
        sa.CheckConstraint(
            "cond_content_type = 'image/jpeg'",
            name="ck_companion_face_asset_content_type",
        ),
        sa.CheckConstraint(
            "cond_size_bytes > 0", name="ck_companion_face_asset_size_positive"
        ),
        sa.ForeignKeyConstraint(
            ["companion_id"], ["companions.companion_id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.owner_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("face_asset_id"),
        sa.UniqueConstraint(
            "companion_id", "version", name="uq_companion_face_asset_version"
        ),
        sa.UniqueConstraint("cond_storage_key"),
    )
    op.create_index(
        "ix_companion_face_assets_companion_id",
        "companion_face_assets",
        ["companion_id"],
    )
    op.create_index(
        "ix_companion_face_assets_owner_id",
        "companion_face_assets",
        ["owner_id"],
    )
    op.create_index(
        "ix_companion_face_assets_state",
        "companion_face_assets",
        ["state"],
    )
    op.create_index(
        "uq_companion_face_asset_active",
        "companion_face_assets",
        ["companion_id"],
        unique=True,
        sqlite_where=sa.text("state = 'active'"),
        postgresql_where=sa.text("state = 'active'"),
    )


def downgrade() -> None:
    op.drop_table("companion_face_assets")
