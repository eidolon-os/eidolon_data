"""Keep one Guard binding identity per owner and physical device."""

from __future__ import annotations

from alembic import op

revision = "0015_guard_binding_identity"
down_revision = "0014_owner_face_profiles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "uq_guard_bindings_owner_device",
        "guard_bindings",
        ["owner_id", "device_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "uq_guard_bindings_owner_device",
        table_name="guard_bindings",
    )
