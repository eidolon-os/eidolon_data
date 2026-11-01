"""Drop channel calibration samples: household calibration by replay is withdrawn.

Design §8.2 (11-01): voices replayed through a loudspeaker carry its own
colouring and do not stand for strangers speaking live in the room, and a
calibration that needs a computer for an hour and a half cannot be
delivered to users. The samples were public speakers' embeddings only; the
measurement they served is kept in the evaluation record. Downgrade
restores the empty table.
"""

import sqlalchemy as sa
from alembic import op

revision = "0030_drop_channel_calibration_samples"
down_revision = "0029_channel_calibration_samples"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_index(
        "ix_channel_calibration_owner_channel_batch", table_name="channel_calibration_samples"
    )
    op.drop_table("channel_calibration_samples")


def downgrade():
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
