"""Add owner/companion consistency constraints."""

from __future__ import annotations

from alembic import op

revision = "0003_owner_companion_constraints"
down_revision = "0002_nullable_device_owner"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("companions") as batch_op:
        batch_op.create_unique_constraint(
            "uq_companions_owner_companion",
            ["owner_id", "companion_id"],
        )

    with op.batch_alter_table("devices") as batch_op:
        batch_op.create_unique_constraint(
            "uq_devices_owner_device",
            ["owner_id", "device_id"],
        )
        batch_op.create_unique_constraint(
            "uq_devices_owner_device_bound_companion",
            ["owner_id", "device_id", "bound_companion_id"],
        )
        batch_op.create_foreign_key(
            "fk_devices_owner_bound_companion",
            "companions",
            ["owner_id", "bound_companion_id"],
            ["owner_id", "companion_id"],
        )

    with op.batch_alter_table("conversations") as batch_op:
        batch_op.create_foreign_key(
            "fk_conversations_owner_companion",
            "companions",
            ["owner_id", "companion_id"],
            ["owner_id", "companion_id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_conversations_owner_device_companion",
            "devices",
            ["owner_id", "device_id", "companion_id"],
            ["owner_id", "device_id", "bound_companion_id"],
        )

    with op.batch_alter_table("memory_realms") as batch_op:
        batch_op.create_foreign_key(
            "fk_memory_realms_owner_companion",
            "companions",
            ["owner_id", "companion_id"],
            ["owner_id", "companion_id"],
            ondelete="CASCADE",
        )

    with op.batch_alter_table("jobs") as batch_op:
        batch_op.create_foreign_key(
            "fk_jobs_owner_companion",
            "companions",
            ["owner_id", "companion_id"],
            ["owner_id", "companion_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch_op:
        batch_op.drop_constraint("fk_jobs_owner_companion", type_="foreignkey")

    with op.batch_alter_table("memory_realms") as batch_op:
        batch_op.drop_constraint("fk_memory_realms_owner_companion", type_="foreignkey")

    with op.batch_alter_table("conversations") as batch_op:
        batch_op.drop_constraint(
            "fk_conversations_owner_device_companion",
            type_="foreignkey",
        )
        batch_op.drop_constraint("fk_conversations_owner_companion", type_="foreignkey")

    with op.batch_alter_table("devices") as batch_op:
        batch_op.drop_constraint("fk_devices_owner_bound_companion", type_="foreignkey")
        batch_op.drop_constraint(
            "uq_devices_owner_device_bound_companion",
            type_="unique",
        )
        batch_op.drop_constraint("uq_devices_owner_device", type_="unique")

    with op.batch_alter_table("companions") as batch_op:
        batch_op.drop_constraint("uq_companions_owner_companion", type_="unique")
