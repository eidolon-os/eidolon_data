"""Retain deleted Person identity for explicit recovery without reauthorization."""

import sqlalchemy as sa
from alembic import op

revision = "0015_person_archive"
down_revision = "0014_voice_enrollment_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "persons", sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false())
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM persons WHERE deleted = 1")).scalar():
        raise RuntimeError("restore archived people before downgrading")
    op.drop_column("persons", "deleted")
