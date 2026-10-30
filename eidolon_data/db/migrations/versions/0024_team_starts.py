"""Serialize saved team deletion and start intent without owning media state."""

import sqlalchemy as sa
from alembic import op

revision = "0024_team_starts"
down_revision = "0023_teams"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("teams") as batch:
        batch.add_column(sa.Column("active_session_id", sa.String(128), nullable=True))
        batch.add_column(
            sa.Column("deleted", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.create_table(
        "team_starts",
        sa.Column("owner_id", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(128), primary_key=True),
        sa.Column("team_id", sa.String(128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("closed", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id", "team_id", "revision"],
            ["team_revisions.owner_id", "team_revisions.team_id", "team_revisions.revision"],
        ),
    )


def downgrade():
    raise RuntimeError("Saved teams must not be discarded or lose pending starts")
