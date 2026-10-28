"""Allow real observation provenance without synthesizing voice enrollments."""
import sqlalchemy as sa
from alembic import op

revision = '0022_speaker_profile_origin'
down_revision = '0021_candidate_activity'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('speaker_profiles') as batch:
        batch.alter_column('enrollment_id', existing_type=sa.String(64), nullable=True)
        batch.add_column(sa.Column('origin', sa.String(16), nullable=False, server_default='enrollment'))
        batch.add_column(sa.Column('source_observation_id', sa.String(64), nullable=True))
        batch.create_foreign_key('fk_speaker_profile_observation', 'candidate_voice_observations',
                                 ['source_observation_id'], ['observation_id'], ondelete='CASCADE')
        batch.create_check_constraint('speaker_profile_origin',
            "(origin='enrollment' AND enrollment_id IS NOT NULL AND source_observation_id IS NULL) OR (origin='observation' AND enrollment_id IS NULL AND source_observation_id IS NOT NULL)")


def downgrade():
    raise RuntimeError('observation profile provenance cannot be converted to fake enrollment')
