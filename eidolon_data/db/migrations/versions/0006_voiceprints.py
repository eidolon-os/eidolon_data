"""Companion voice policies, speaker templates and bounded enrollment sessions."""

from alembic import op
import sqlalchemy as sa

revision = "0006_voiceprints"
down_revision = "0005_conversation_preferences"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "speaker_profiles",
        sa.Column("profile_id", sa.String(64), primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(64),
            sa.ForeignKey("owners.owner_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("enrollment_id", sa.String(64), nullable=False),
        sa.Column("template_json", sa.JSON, nullable=False),
    )
    op.create_index("ix_speaker_profiles_owner_id", "speaker_profiles", ["owner_id"])
    op.create_table(
        "companion_voice_policies",
        sa.Column(
            "companion_id",
            sa.String(64),
            sa.ForeignKey("companions.companion_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("mode", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
    )
    op.create_table(
        "companion_speaker_bindings",
        sa.Column(
            "companion_id",
            sa.String(64),
            sa.ForeignKey("companions.companion_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column(
            "profile_id",
            sa.String(64),
            sa.ForeignKey("speaker_profiles.profile_id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    op.create_table(
        "voice_enrollments",
        sa.Column("enrollment_id", sa.String(64), primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(64),
            sa.ForeignKey("owners.owner_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "companion_id",
            sa.String(64),
            sa.ForeignKey("companions.companion_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("profile_id", sa.String(64), nullable=False),
        sa.Column("request_json", sa.JSON, nullable=False),
        sa.Column("samples_json", sa.JSON, nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("expires_at", sa.Float, nullable=False),
    )
    op.create_index("ix_voice_enrollments_owner_id", "voice_enrollments", ["owner_id"])


def downgrade():
    connection = op.get_bind()
    if (
        connection.execute(sa.text("SELECT count(*) FROM speaker_profiles")).scalar()
        or connection.execute(
            sa.text("SELECT count(*) FROM companion_voice_policies WHERE mode = 'allowlist'")
        ).scalar()
    ):
        raise RuntimeError(
            "voiceprint downgrade requires explicit policy/data export and migration"
        )
    for name in (
        "voice_enrollments",
        "companion_speaker_bindings",
        "companion_voice_policies",
        "speaker_profiles",
    ):
        op.drop_table(name)
