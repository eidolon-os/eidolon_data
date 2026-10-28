"""Anchor candidate pool retention without deleting retained Person data."""
import sqlalchemy as sa
from alembic import op

revision = '0021_candidate_activity'
down_revision = '0020_candidate_voice_observations'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column('persons', sa.Column('candidate_activity_at', sa.Float(), nullable=True))
    # Historical candidates have no trustworthy creation timestamp. Start their
    # retention window at migration, explicitly not a claimed speech timestamp.
    op.execute("UPDATE persons SET candidate_activity_at=CAST(strftime('%s','now') AS REAL) WHERE maturity='candidate'")
    op.create_index('ix_person_candidate_activity', 'persons',
                    ['owner_id', 'maturity', 'recognition_state', 'candidate_activity_at'])


def downgrade():
    raise RuntimeError('candidate activity retention baseline must not be discarded')
