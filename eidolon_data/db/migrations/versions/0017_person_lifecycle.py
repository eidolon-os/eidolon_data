"""Separate archives, unidentified retained memory and permanent deletion receipts."""

import sqlalchemy as sa
from alembic import op

revision = "0017_person_lifecycle"
down_revision = "0016_retained_person_archives"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "persons",
        sa.Column("identity_state", sa.String(16), nullable=False, server_default="known"),
    )
    op.add_column("persons", sa.Column("archived_at", sa.String(40), nullable=True))
    # Only the exact tombstone synthesized by 0016 has this combination.
    op.execute(
        "UPDATE persons SET identity_state='missing' WHERE deleted=true AND display_name='已删除用户（历史档案）' AND profile_json='{}' AND revision=1"
    )
    op.create_table(
        "person_deletions",
        sa.Column("person_id", sa.String(64), primary_key=True),
        sa.Column(
            "owner_id",
            sa.String(64),
            sa.ForeignKey("owners.owner_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("realm_id", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
    )


def downgrade():
    raise RuntimeError("permanent deletion receipts must not be discarded by downgrade")
