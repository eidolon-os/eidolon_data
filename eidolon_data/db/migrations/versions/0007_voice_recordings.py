"""Original successful enrollment trial recordings for owner-only playback."""
from alembic import op
import sqlalchemy as sa

revision = "0007_voice_recordings"
down_revision = "0006_voiceprints"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "speaker_recordings",
        sa.Column("profile_id", sa.String(64),
                  sa.ForeignKey("speaker_profiles.profile_id", ondelete="CASCADE"),
                  primary_key=True),
        sa.Column("pcm", sa.LargeBinary, nullable=False),
    )


def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM speaker_recordings")).scalar():
        raise RuntimeError("Export or explicitly delete reference recordings before downgrade")
    op.drop_table("speaker_recordings")
