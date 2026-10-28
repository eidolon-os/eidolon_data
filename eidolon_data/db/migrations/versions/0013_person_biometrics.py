"""Enforce one voice per Person with a fresh enrollment baseline."""
import sqlalchemy as sa
from alembic import op

revision = "0013_person_biometrics"
down_revision = "0012_person_memory_realms"
branch_labels = None
depends_on = None

def upgrade():
    # Pre-single-profile evidence is intentionally retired; users re-enroll.
    op.execute("DELETE FROM voice_enrollments")
    op.execute("DELETE FROM speaker_recordings")
    op.execute("DELETE FROM speaker_profiles")
    op.create_index("uq_speaker_profile_person", "speaker_profiles", ["owner_id", "person_id"], unique=True)
    op.create_table("person_face_profiles",
        sa.Column("person_id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("storage_key", sa.String(256), unique=True, nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("model_id", sa.String(128), nullable=True),
        sa.Column("preprocessing_version", sa.String(128), nullable=True),
        sa.Column("feature_schema_version", sa.Integer(), nullable=True),
        sa.Column("features_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(["owner_id", "person_id"], ["persons.owner_id", "persons.person_id"],
            name="fk_person_face_owner_person", ondelete="CASCADE"))

def downgrade():
    if op.get_bind().execute(sa.text("SELECT count(*) FROM person_face_profiles")).scalar():
        raise RuntimeError("export Person face profiles before downgrade")
    op.drop_table("person_face_profiles")
    op.drop_index("uq_speaker_profile_person", table_name="speaker_profiles")
