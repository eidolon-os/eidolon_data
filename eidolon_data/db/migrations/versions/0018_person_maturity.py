"""Keep evidence maturity and naming independent of Person activation."""

import sqlalchemy as sa
from alembic import op

revision = "0018_person_maturity"
down_revision = "0017_person_lifecycle"
branch_labels = None
depends_on = None


def upgrade():
    # Existing people were explicitly created through management/enrollment.
    # Retained identity_state=missing archives remain missing and inactive.
    with op.batch_alter_table("persons") as batch:
        batch.add_column(sa.Column("maturity", sa.String(16), nullable=False,
                                   server_default="established"))
        batch.add_column(sa.Column("is_named", sa.Boolean(), nullable=False,
                                   server_default=sa.true()))
        batch.create_check_constraint("person_maturity", "maturity IN ('candidate', 'established')")


def downgrade():
    raise RuntimeError("dropping maturity would grant candidates established identity semantics")
