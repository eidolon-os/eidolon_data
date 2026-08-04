"""Add durable Guard runtime delivery queue.

Revision ID: 0011_guard_runtime_delivery
Revises: 0010_guard_runtime_config
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_guard_runtime_delivery"
down_revision = "0010_guard_runtime_config"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "guard_runtime_deliveries",
        sa.Column("delivery_id", sa.String(length=96), nullable=False),
        sa.Column("binding_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("runtime_revision", sa.Integer(), nullable=False),
        sa.Column("desired_runtime_state", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="pending"),
        sa.Column("command_id", sa.String(length=64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["binding_id"], ["guard_bindings.binding_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.owner_id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("delivery_id"),
        sa.UniqueConstraint("binding_id", "runtime_revision", name="uq_guard_runtime_delivery_revision"),
        sa.UniqueConstraint("command_id"),
    )
    op.create_index(
        "ix_guard_runtime_deliveries_device_status",
        "guard_runtime_deliveries",
        ["device_id", "status"],
    )
    op.create_index(
        "ix_guard_runtime_deliveries_binding_created",
        "guard_runtime_deliveries",
        ["binding_id", "created_at"],
    )
    op.create_index(
        "ix_guard_runtime_deliveries_lease_expires_at",
        "guard_runtime_deliveries",
        ["lease_expires_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_guard_runtime_deliveries_lease_expires_at", table_name="guard_runtime_deliveries")
    op.drop_index("ix_guard_runtime_deliveries_binding_created", table_name="guard_runtime_deliveries")
    op.drop_index("ix_guard_runtime_deliveries_device_status", table_name="guard_runtime_deliveries")
    op.drop_table("guard_runtime_deliveries")
