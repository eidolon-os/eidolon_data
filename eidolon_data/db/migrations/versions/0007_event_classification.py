"""Add event classification / correlation columns (audit-log hardening).

Adds tier/source/severity/outcome + reason, companion_id, trace_id,
data_classification, schema_version, occurred_at. NOT NULL columns carry a
server_default so existing rows backfill; occurred_at is nullable (readers fall
back to created_at for legacy rows). See docs/跨系统/事件审计追踪补全方案.md §3–4.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0007_event_classification"
down_revision = "0006_turn_trace_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("events") as batch_op:
        batch_op.add_column(sa.Column("companion_id", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("event_class", sa.String(length=8), nullable=False, server_default="audit")
        )
        batch_op.add_column(
            sa.Column("source", sa.String(length=16), nullable=False, server_default="data")
        )
        batch_op.add_column(
            sa.Column("severity", sa.String(length=8), nullable=False, server_default="info")
        )
        batch_op.add_column(
            sa.Column("outcome", sa.String(length=12), nullable=False, server_default="success")
        )
        batch_op.add_column(sa.Column("reason", sa.String(length=256), nullable=True))
        batch_op.add_column(sa.Column("trace_id", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column(
                "data_classification", sa.String(length=10), nullable=False, server_default="safe"
            )
        )
        batch_op.add_column(
            sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True))

        batch_op.create_index("ix_events_companion_id", ["companion_id"])
        batch_op.create_index("ix_events_event_class", ["event_class"])
        batch_op.create_index("ix_events_source", ["source"])
        batch_op.create_index("ix_events_severity", ["severity"])
        batch_op.create_index("ix_events_outcome", ["outcome"])
        batch_op.create_index("ix_events_trace_id", ["trace_id"])
        batch_op.create_index("ix_events_owner_created", ["owner_id", "created_at"])

    # Backfill occurred_at from the record timestamp for pre-existing rows.
    op.execute("UPDATE events SET occurred_at = created_at WHERE occurred_at IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("events") as batch_op:
        batch_op.drop_index("ix_events_owner_created")
        batch_op.drop_index("ix_events_trace_id")
        batch_op.drop_index("ix_events_outcome")
        batch_op.drop_index("ix_events_severity")
        batch_op.drop_index("ix_events_source")
        batch_op.drop_index("ix_events_event_class")
        batch_op.drop_index("ix_events_companion_id")
        batch_op.drop_column("occurred_at")
        batch_op.drop_column("schema_version")
        batch_op.drop_column("data_classification")
        batch_op.drop_column("trace_id")
        batch_op.drop_column("reason")
        batch_op.drop_column("outcome")
        batch_op.drop_column("severity")
        batch_op.drop_column("source")
        batch_op.drop_column("event_class")
        batch_op.drop_column("companion_id")
