"""Bind a release evaluation to the existing Companion voice policy."""

import sqlalchemy as sa
from alembic import op

revision = "0008_voice_admission"
down_revision = "0007_voice_recordings"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("companion_voice_policies", sa.Column("approval_json", sa.JSON, nullable=True))


def downgrade():
    if (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM companion_voice_policies WHERE mode = 'allowlist'"))
        .scalar()
    ):
        raise RuntimeError("disable voice admission before downgrading its authority")
    op.drop_column("companion_voice_policies", "approval_json")
