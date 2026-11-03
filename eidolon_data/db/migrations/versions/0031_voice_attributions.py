"""Voice attributions and per-channel observation templates (design §3.2, §3.5; A1).

Recognition submits verdicts; Data records which Person the voice heard in a
turn belongs to, and keeps a Person's observation templates per capture
channel from attributed conversation tracks. Downgrade drops both: the
templates are rebuilt from later conversations, and the attributions only
ever named turns whose content was already written under that Person.
"""

import sqlalchemy as sa
from alembic import op

revision = "0031_voice_attributions"
down_revision = "0030_drop_channel_calibration_samples"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "voice_attributions",
        sa.Column("attribution_id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("person_id", sa.String(64), nullable=False),
        sa.Column("person_revision", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("refs_json", sa.JSON(), nullable=False),
        sa.Column("verdict_digest", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("recorded_at", sa.Float(), nullable=False),
        sa.Column("change_seq", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.owner_id"], ondelete="CASCADE"),
        sa.CheckConstraint("source IN ('turn','accumulated','owner')", name="voice_attribution_source"),
    )
    op.create_index("uq_voice_attribution_change", "voice_attributions", ["owner_id", "change_seq"],
                    unique=True)
    op.create_index("ix_voice_attribution_person", "voice_attributions", ["owner_id", "person_id"])
    op.create_table(
        "voice_attribution_refs",
        sa.Column("owner_id", sa.String(64), primary_key=True),
        sa.Column("session_id", sa.String(128), primary_key=True),
        sa.Column("turn_id", sa.String(128), primary_key=True),
        sa.Column("attribution_id", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["attribution_id"], ["voice_attributions.attribution_id"],
                                ondelete="CASCADE"),
    )
    op.create_index("ix_voice_attribution_refs_attribution_id", "voice_attribution_refs",
                    ["attribution_id"])
    op.create_table(
        "voice_observation_templates",
        sa.Column("template_id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("person_id", sa.String(64), nullable=False),
        sa.Column("channel", sa.String(96), nullable=False),
        sa.Column("session_id", sa.String(128), nullable=False),
        sa.Column("track_id", sa.String(64), nullable=False),
        sa.Column("attribution_id", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("sample_json", sa.JSON(), nullable=False),
        sa.Column("updated_at", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(["owner_id", "person_id"], ["persons.owner_id", "persons.person_id"],
                                name="fk_voice_observation_templates_owner_person", ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["attribution_id"], ["voice_attributions.attribution_id"],
                                ondelete="CASCADE"),
    )
    op.create_index("uq_voice_observation_template_track", "voice_observation_templates",
                    ["owner_id", "person_id", "channel", "session_id", "track_id"], unique=True)
    op.create_index("ix_voice_observation_template_channel", "voice_observation_templates",
                    ["owner_id", "person_id", "channel"])
    op.create_index("ix_voice_observation_templates_attribution_id", "voice_observation_templates",
                    ["attribution_id"])


def downgrade():
    op.drop_index("ix_voice_observation_templates_attribution_id",
                  table_name="voice_observation_templates")
    op.drop_index("ix_voice_observation_template_channel", table_name="voice_observation_templates")
    op.drop_index("uq_voice_observation_template_track", table_name="voice_observation_templates")
    op.drop_table("voice_observation_templates")
    op.drop_index("ix_voice_attribution_refs_attribution_id", table_name="voice_attribution_refs")
    op.drop_table("voice_attribution_refs")
    op.drop_index("ix_voice_attribution_person", table_name="voice_attributions")
    op.drop_index("uq_voice_attribution_change", table_name="voice_attributions")
    op.drop_table("voice_attributions")
