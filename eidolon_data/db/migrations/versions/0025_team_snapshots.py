"""Pin resolved execution selections before dispatch, preserving pending starts."""
import sqlalchemy as sa
from alembic import op
revision = "0025_team_snapshots"
down_revision = "0024_team_starts"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("team_starts") as batch:
        batch.add_column(sa.Column("selection", sa.JSON(), nullable=True))
        batch.add_column(sa.Column("terminal_status", sa.JSON(), nullable=True))


def downgrade():
    raise RuntimeError("Saved teams must not be discarded or lose execution snapshots")
