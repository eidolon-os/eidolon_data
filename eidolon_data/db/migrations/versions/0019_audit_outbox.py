"""Add the authority-local audit outbox.

The global audit ledger is an independent projection. This table only bridges
the local domain transaction to a durable transport and can be purged after
acknowledgement.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0019_audit_outbox"
down_revision = "0018_companion_face_idle_clip"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "audit_outbox",
        sa.Column("outbox_id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("producer", sa.String(length=64), nullable=False),
        sa.Column("category", sa.String(length=16), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=True),
        sa.Column("subject_type", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=128), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("severity", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(length=256), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("data_classification", sa.String(length=16), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "category IN ('governance', 'receipt')",
            name="ck_audit_outbox_category",
        ),
        sa.CheckConstraint(
            "outcome IN ('success', 'failure', 'denied', 'deferred')",
            name="ck_audit_outbox_outcome",
        ),
        sa.CheckConstraint(
            "severity IN ('info', 'warn', 'error', 'critical')",
            name="ck_audit_outbox_severity",
        ),
        sa.CheckConstraint("attempt_count >= 0", name="ck_audit_outbox_attempt_count"),
        sa.PrimaryKeyConstraint("outbox_id", name=op.f("pk_audit_outbox")),
        sa.UniqueConstraint("event_id", name=op.f("uq_audit_outbox_event_id")),
    )
    op.create_index(
        "ix_audit_outbox_pending",
        "audit_outbox",
        ["published_at", "next_attempt_at", "outbox_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_audit_outbox_pending", table_name="audit_outbox")
    op.drop_table("audit_outbox")
