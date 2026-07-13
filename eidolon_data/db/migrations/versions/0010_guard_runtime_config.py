"""Separate Guard device runtime configuration from Hub policy configuration."""

from alembic import op
import sqlalchemy as sa


revision = "0010_guard_runtime_config"
down_revision = "0009_data_contract_alignment"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "guard_bindings",
        sa.Column("runtime_revision", sa.Integer(), nullable=False, server_default="1"),
    )
    op.add_column(
        "guard_bindings",
        sa.Column("runtime_config_json", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.add_column(
        "guard_bindings",
        sa.Column(
            "desired_runtime_state",
            sa.String(length=16),
            nullable=False,
            server_default="stopped",
        ),
    )


def downgrade() -> None:
    op.drop_column("guard_bindings", "desired_runtime_state")
    op.drop_column("guard_bindings", "runtime_config_json")
    op.drop_column("guard_bindings", "runtime_revision")
