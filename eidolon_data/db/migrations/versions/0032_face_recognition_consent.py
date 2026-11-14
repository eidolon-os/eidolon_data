"""Face recognition the Owner turns on: the consent and the weights behind a feature.

A stored photo grants no recognition. The consent columns record what the Owner
agreed to and when; ``weights_hash`` pins the exact model a feature came from.
Downgrade also clears the features: without these columns nothing would say
they were ever agreed to.
"""

import sqlalchemy as sa
from alembic import op

revision = "0032_face_recognition_consent"
down_revision = "0031_voice_attributions"
branch_labels = None
depends_on = None

COLUMNS = ("weights_hash", "recognition_consent_version", "recognition_consent_statement",
           "recognition_consent_at")


def upgrade():
    with op.batch_alter_table("person_face_profiles") as batch:
        batch.add_column(sa.Column("weights_hash", sa.String(64), nullable=True))
        batch.add_column(sa.Column("recognition_consent_version", sa.String(64), nullable=True))
        batch.add_column(sa.Column("recognition_consent_statement", sa.Text(), nullable=True))
        batch.add_column(sa.Column("recognition_consent_at", sa.String(40), nullable=True))


def downgrade():
    op.execute(
        "UPDATE person_face_profiles SET model_id = NULL, preprocessing_version = NULL, "
        "feature_schema_version = NULL, features_json = NULL"
    )
    with op.batch_alter_table("person_face_profiles") as batch:
        for name in COLUMNS:
            batch.drop_column(name)
