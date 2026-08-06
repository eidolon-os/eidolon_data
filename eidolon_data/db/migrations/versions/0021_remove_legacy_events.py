"""Remove the legacy events table after audit-outbox cutover."""

from __future__ import annotations

from alembic import op

revision = "0021_remove_legacy_events"
down_revision = "0020_remove_agent_runtime_authority"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("events")


def downgrade() -> None:
    raise RuntimeError(
        "Legacy events table cutover is irreversible; use the global audit index"
    )
