"""Owner voices (designed or cloned), their provider bindings, and provider tombstones."""

import sqlalchemy as sa
from alembic import op

revision = "0027_owner_voices"
down_revision = "0026_retire_companion_voice"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "owner_voices",
        sa.Column("voice_asset_id", sa.String(64), primary_key=True),
        sa.Column("owner_id", sa.String(64), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("method", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("design_prompt", sa.Text(), nullable=True),
        sa.Column("design_preview_text", sa.Text(), nullable=True),
        sa.Column("consent_json", sa.JSON(), nullable=True),
        sa.Column("reference_storage_key", sa.String(256), nullable=True, unique=True),
        sa.Column("preview_storage_key", sa.String(256), nullable=True, unique=True),
        sa.Column("create_operation_id", sa.String(128), nullable=False),
        sa.Column("draft_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.owner_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("owner_id", "create_operation_id", name="uq_owner_voice_operation"),
        sa.CheckConstraint("method IN ('design', 'clone')", name="owner_voice_method"),
        sa.CheckConstraint("state IN ('draft', 'active')", name="owner_voice_state"),
        sa.CheckConstraint("revision > 0", name="owner_voice_revision_positive"),
    )
    op.create_index("ix_owner_voices_owner_id", "owner_voices", ["owner_id"])
    op.create_table(
        "owner_voice_bindings",
        sa.Column("provider", sa.String(64), primary_key=True),
        sa.Column("provider_voice_id", sa.String(256), primary_key=True),
        sa.Column("voice_asset_id", sa.String(64), nullable=False),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_detail", sa.String(200), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["voice_asset_id"], ["owner_voices.voice_asset_id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint("voice_asset_id", "provider", "model", name="uq_owner_voice_binding_model"),
        sa.CheckConstraint(
            "status IN ('deploying', 'ready', 'rejected', 'failed')", name="owner_voice_binding_status"
        ),
    )
    op.create_index(
        "ix_owner_voice_bindings_voice_asset_id", "owner_voice_bindings", ["voice_asset_id"]
    )
    op.create_table(
        "provider_voice_tombstones",
        sa.Column("provider", sa.String(64), primary_key=True),
        sa.Column("provider_voice_id", sa.String(256), primary_key=True),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "reason IN ('deleted', 'replaced', 'owner_deleted', 'draft_expired')",
            name="provider_voice_tombstone_reason",
        ),
    )


def downgrade():
    connection = op.get_bind()
    for table in ("owner_voices", "provider_voice_tombstones"):
        if connection.execute(sa.text(f"SELECT 1 FROM {table} LIMIT 1")).first() is not None:
            raise RuntimeError(
                "Delete Owner voices at their provider before downgrade; they would be orphaned"
            )
    op.drop_table("provider_voice_tombstones")
    op.drop_index("ix_owner_voice_bindings_voice_asset_id", table_name="owner_voice_bindings")
    op.drop_table("owner_voice_bindings")
    op.drop_index("ix_owner_voices_owner_id", table_name="owner_voices")
    op.drop_table("owner_voices")
