"""Remove Agent runtime tables from the System Data authority."""

from __future__ import annotations

from alembic import op

revision = "0020_remove_agent_runtime_authority"
down_revision = "0019_audit_outbox"
branch_labels = None
depends_on = None

_NAMING_CONVENTION = {
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
}


def upgrade() -> None:
    # runtime_session_id remains an opaque correlation value. Rebuilding the
    # table removes its cross-authority FK without discarding body-command rows.
    with op.batch_alter_table(
        "body_commands",
        recreate="always",
        naming_convention=_NAMING_CONVENTION,
    ) as batch_op:
        batch_op.drop_constraint(
            "fk_body_commands_runtime_session_id_runtime_sessions",
            type_="foreignkey",
        )

    op.drop_table("messages")
    op.drop_table("turns")
    op.drop_table("conversations")
    op.drop_table("jobs")
    op.drop_table("runtime_sessions")


def downgrade() -> None:
    raise RuntimeError(
        "Agent runtime authority cutover is irreversible; create a fresh V2 database"
    )
