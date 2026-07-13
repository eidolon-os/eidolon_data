"""Add ATK Guard control-plane bindings."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_guard_control_plane"
down_revision = "0006_turn_trace_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guard_bindings",
        sa.Column("binding_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("guard_companion_id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("policy_id", sa.String(length=64), nullable=False),
        sa.Column("config_revision", sa.Integer(), nullable=False),
        sa.Column("config_json", sa.JSON(), nullable=False),
        sa.Column("status_json", sa.JSON(), nullable=False),
        sa.Column("activated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("disabled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.owner_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_id", "guard_companion_id"],
            ["companions.owner_id", "companions.companion_id"],
            name="fk_guard_bindings_owner_guard_companion",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["device_id"], ["devices.device_id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("binding_id"),
    )
    op.create_index("ix_guard_bindings_owner_id", "guard_bindings", ["owner_id"])
    op.create_index("ix_guard_bindings_guard_companion_id", "guard_bindings", ["guard_companion_id"])
    op.create_index("ix_guard_bindings_device_id", "guard_bindings", ["device_id"])
    op.create_index("ix_guard_bindings_state", "guard_bindings", ["state"])
    op.create_index("ix_guard_bindings_owner_state", "guard_bindings", ["owner_id", "state"])
    op.create_index(
        "uq_guard_bindings_owner_active",
        "guard_bindings",
        ["owner_id"],
        unique=True,
        sqlite_where=sa.text("state = 'active'"),
        postgresql_where=sa.text("state = 'active'"),
    )
    op.create_index(
        "uq_guard_bindings_device_active",
        "guard_bindings",
        ["device_id"],
        unique=True,
        sqlite_where=sa.text("state = 'active'"),
        postgresql_where=sa.text("state = 'active'"),
    )


def downgrade() -> None:
    op.drop_table("guard_bindings")
