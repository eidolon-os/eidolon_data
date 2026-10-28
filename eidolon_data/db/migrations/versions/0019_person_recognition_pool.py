"""Retain candidates outside automatic matching without deleting their data."""

import sqlalchemy as sa
from alembic import op

revision = "0019_person_recognition_pool"
down_revision = "0018_person_maturity"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("persons") as batch:
        batch.add_column(sa.Column("recognition_state", sa.String(16), nullable=False,
                                   server_default="active"))
        batch.create_check_constraint("person_recognition_state",
                                      "recognition_state IN ('active', 'retained')")


def downgrade():
    raise RuntimeError("retained recognition exclusions must not be discarded by downgrade")
