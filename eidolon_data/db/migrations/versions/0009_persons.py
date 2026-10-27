"""Add OS people without inferring identities from existing voiceprints."""

import sqlalchemy as sa
from alembic import op

revision = "0009_persons"
down_revision = "0008_voice_admission"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "persons",
        sa.Column("person_id", sa.String(64), primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(64),
            sa.ForeignKey("owners.owner_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("profile_json", sa.JSON, nullable=False),
        sa.CheckConstraint("status IN ('active', 'inactive')", name="person_status"),
        sa.CheckConstraint("revision > 0", name="person_revision_positive"),
    )
    op.create_index("ix_persons_owner_id", "persons", ["owner_id"])


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM persons")).scalar():
        raise RuntimeError("person downgrade requires explicit data export and migration")
    op.drop_table("persons")
