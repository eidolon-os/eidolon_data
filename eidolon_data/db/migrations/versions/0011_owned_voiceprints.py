"""Require Person ownership; retire unassigned legacy biometric evidence."""

from alembic import op
import sqlalchemy as sa

revision = "0011_owned_voiceprints"
down_revision = "0010_person_voiceprints"
branch_labels = None
depends_on = None


def upgrade():
    connection = op.get_bind()
    # An existing, explicit owner mismatch is corruption, not legacy data.
    # Never silently delete or reassign evidence that already names a Person.
    for table in ("speaker_profiles", "voice_enrollments"):
        invalid = connection.execute(sa.text(
            f"SELECT count(*) FROM {table} v WHERE v.person_id IS NOT NULL AND NOT EXISTS "
            "(SELECT 1 FROM persons p WHERE p.person_id=v.person_id AND p.owner_id=v.owner_id)"
        )).scalar_one()
        if invalid:
            raise RuntimeError(f"{table}: review mismatched Person ownership before migration")

    # The retired linking workflow has no runtime successor. Only evidence
    # without a Person is removed; assigned evidence is retained byte-for-byte.
    op.execute("DELETE FROM speaker_recordings WHERE profile_id IN "
               "(SELECT profile_id FROM speaker_profiles WHERE person_id IS NULL)")
    op.execute("DELETE FROM voice_enrollments WHERE person_id IS NULL")
    op.execute("DELETE FROM speaker_profiles WHERE person_id IS NULL")
    op.create_index("uq_persons_owner_person", "persons", ["owner_id", "person_id"], unique=True)

    # SQLite batch rebuild can cascade recordings; preserve the retained rows.
    op.execute("CREATE TEMP TABLE retained_voice_recordings AS "
               "SELECT profile_id, pcm FROM speaker_recordings")
    for table in ("speaker_profiles", "voice_enrollments"):
        with op.batch_alter_table(table) as batch:
            batch.drop_constraint(f"fk_{table}_person_id_persons", type_="foreignkey")
            batch.alter_column("person_id", existing_type=sa.String(64), nullable=False)
            batch.create_foreign_key(
                f"fk_{table}_owner_person", "persons", ["owner_id", "person_id"],
                ["owner_id", "person_id"], ondelete="CASCADE",
            )
    op.execute("INSERT OR IGNORE INTO speaker_recordings(profile_id, pcm) "
               "SELECT profile_id, pcm FROM retained_voice_recordings")
    op.execute("DROP TABLE retained_voice_recordings")


def downgrade():
    raise RuntimeError("unassigned voiceprints are retired; restore a reviewed backup instead")
