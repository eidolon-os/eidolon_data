"""Retire old voice evidence on hosts already running revision 0013."""
from alembic import op

revision = "0014_voice_enrollment_baseline"
down_revision = "0013_person_biometrics"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("DELETE FROM voice_enrollments")
    op.execute("DELETE FROM speaker_recordings")
    op.execute("DELETE FROM speaker_profiles")


def downgrade():
    # Data retirement is irreversible; there is no schema change to undo.
    pass
