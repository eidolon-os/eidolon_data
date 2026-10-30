"""Add saved teams and immutable revisions without changing existing assets."""

import sqlalchemy as sa
from alembic import op

revision = "0023_teams"
down_revision = "0022_speaker_profile_origin"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "teams",
        sa.Column("owner_id", sa.String(64), primary_key=True),
        sa.Column("team_id", sa.String(128), primary_key=True),
        sa.Column("host_id", sa.String(128), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.owner_id"]),
        sa.CheckConstraint("revision > 0", name="team_revision_positive"),
    )
    op.create_table(
        "team_revisions",
        sa.Column("owner_id", sa.String(64), primary_key=True),
        sa.Column("team_id", sa.String(128), primary_key=True),
        sa.Column("revision", sa.Integer(), primary_key=True),
        sa.Column("configuration", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id", "team_id"], ["teams.owner_id", "teams.team_id"]),
        sa.CheckConstraint("revision > 0", name="team_saved_revision_positive"),
    )


def downgrade():
    raise RuntimeError("Saved teams must not be discarded by downgrade")
