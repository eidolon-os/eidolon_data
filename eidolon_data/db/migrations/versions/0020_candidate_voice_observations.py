"""Persist bounded independent source receipts for candidate voice evidence."""
import sqlalchemy as sa
from alembic import op

revision = '0020_candidate_voice_observations'
down_revision = '0019_person_recognition_pool'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('candidate_voice_observations',
        sa.Column('observation_id', sa.String(64), primary_key=True),
        *[sa.Column(name, sa.String(size), nullable=False) for name, size in [
            ('owner_id', 64), ('person_id', 64), ('session_id', 128), ('turn_id', 128),
            ('audio_sha256', 64), ('profile_id', 64), ('request_digest', 64)]],
        sa.Column('profile_revision', sa.Integer(), nullable=False),
        sa.Column('observation_json', sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(['owner_id', 'person_id'], ['persons.owner_id', 'persons.person_id'], ondelete='CASCADE'))
    op.create_index('uq_candidate_voice_turn', 'candidate_voice_observations', ['owner_id', 'session_id', 'turn_id'], unique=True)
    op.create_index('uq_candidate_voice_audio', 'candidate_voice_observations', ['owner_id', 'audio_sha256'], unique=True)
    op.create_index('ix_candidate_voice_person', 'candidate_voice_observations', ['owner_id', 'person_id'])


def downgrade():
    raise RuntimeError('candidate source receipts cannot be dropped without losing replay protection')
