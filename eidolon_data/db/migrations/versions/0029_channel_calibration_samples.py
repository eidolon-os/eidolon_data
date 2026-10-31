"""Channel calibration samples: public speech replayed in a household (design §8.2, 1c-a).

Embeddings and their registration-style windows of public speakers, captured
through one Owner's channel as in-domain impostor evidence. Never a household
member's voice and never audio. Downgrade discards them: they are re-collected
by replaying the same public recordings.
"""

import sqlalchemy as sa
from alembic import op

revision = "0029_channel_calibration_samples"
down_revision = "0028_registration_template_speech"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "channel_calibration_samples",
        sa.Column("sample_id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(96), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("batch_id", sa.String(64), nullable=False),
        sa.Column("captured_at", sa.Float(), nullable=False),
        sa.Column("sample_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.owner_id"], ondelete="CASCADE"),
        sa.CheckConstraint("source = 'public_replay'", name="channel_calibration_source"),
    )
    op.create_index(
        "ix_channel_calibration_owner_channel_batch",
        "channel_calibration_samples",
        ["owner_id", "channel", "batch_id"],
    )


def downgrade():
    op.drop_index(
        "ix_channel_calibration_owner_channel_batch", table_name="channel_calibration_samples"
    )
    op.drop_table("channel_calibration_samples")
