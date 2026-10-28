"""Keep the Owner default and add explicit personal Realm catalog pointers."""

import sqlalchemy as sa
from alembic import op

revision = "0012_person_memory_realms"
down_revision = "0011_owned_voiceprints"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("memory_realms", sa.Column("person_id", sa.String(64), nullable=True))
    op.drop_index("uq_memory_realms_owner_active", table_name="memory_realms")
    op.create_index(
        "uq_memory_realms_owner_active",
        "memory_realms",
        ["owner_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active' AND person_id IS NULL"),
        postgresql_where=sa.text("status = 'active' AND person_id IS NULL"),
    )
    op.create_index(
        "uq_memory_realms_person", "memory_realms", ["owner_id", "person_id"], unique=True
    )


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM memory_realms WHERE person_id IS NOT NULL"))
        .scalar()
    ):
        raise RuntimeError("personal Realms require explicit export before downgrade")
    op.drop_index("uq_memory_realms_person", table_name="memory_realms")
    op.drop_index("uq_memory_realms_owner_active", table_name="memory_realms")
    op.drop_column("memory_realms", "person_id")
    op.create_index(
        "uq_memory_realms_owner_active",
        "memory_realms",
        ["owner_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )
