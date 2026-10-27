"""Retire Companion admission; preserve unassigned biometric evidence for explicit linking."""

from alembic import op
import sqlalchemy as sa

revision = "0010_person_voiceprints"
down_revision = "0009_persons"
branch_labels = None
depends_on = None


def upgrade():
    # Preserve child recordings explicitly: SQLite parent rebuild can cascade.
    op.execute(
        "CREATE TEMP TABLE voice_recording_migration AS SELECT profile_id, pcm FROM speaker_recordings"
    )
    with op.batch_alter_table("speaker_profiles") as batch:
        batch.add_column(sa.Column("person_id", sa.String(64), nullable=True))
        batch.create_foreign_key(
            "fk_speaker_profiles_person_id_persons",
            "persons",
            ["person_id"],
            ["person_id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_speaker_profiles_person_id", ["person_id"])
    op.execute(
        "INSERT OR IGNORE INTO speaker_recordings(profile_id, pcm) SELECT profile_id, pcm FROM voice_recording_migration"
    )
    op.execute("DROP TABLE voice_recording_migration")
    # Enrollment is not referenced by other tables. Rebuild only this child.
    with op.batch_alter_table(
        "voice_enrollments",
        naming_convention={"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"},
    ) as batch:
        batch.drop_constraint("fk_voice_enrollments_companion_id_companions", type_="foreignkey")
        batch.drop_column("companion_id")
        batch.add_column(sa.Column("person_id", sa.String(64), nullable=True))
        batch.create_foreign_key(
            "fk_voice_enrollments_person_id_persons",
            "persons",
            ["person_id"],
            ["person_id"],
            ondelete="CASCADE",
        )
        batch.create_index("ix_voice_enrollments_person_id", ["person_id"])
    op.execute(
        "UPDATE voice_enrollments SET state='cancelled', samples_json='{}' WHERE state='collecting'"
    )
    op.drop_table("companion_speaker_bindings")
    op.drop_table("companion_voice_policies")


def downgrade():
    raise RuntimeError(
        "Companion admission is retired; restore a reviewed backup with a non-gating release"
    )
