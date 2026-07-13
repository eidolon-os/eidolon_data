"""Align persistent data schema with canonical owner/persona/event contracts."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_data_contract_alignment"
down_revision = "0008_guard_policy_action_outbox"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("companions") as batch_op:
        batch_op.add_column(
            sa.Column("companion_type", sa.String(length=16), nullable=False, server_default="slave")
        )
        batch_op.create_index("ix_companions_companion_type", ["companion_type"])
    op.execute(
        "UPDATE companions SET companion_type = "
        "CASE WHEN is_master THEN 'master' ELSE 'slave' END"
    )

    # Legacy prompt/compiler records do not encode a canonical semantic persona.
    # Discarding them is intentional: inferring a new identity snapshot from the
    # old DSL would make the migration silently invent persona state.
    op.execute("UPDATE companions SET current_genome_id = NULL")
    op.execute("DELETE FROM persona_genomes")
    with op.batch_alter_table("persona_genomes") as batch_op:
        batch_op.add_column(
            sa.Column(
                "schema_version",
                sa.String(length=64),
                nullable=False,
                server_default="eidolon.persona_genome",
            )
        )
        batch_op.add_column(sa.Column("genome_hash", sa.String(length=80), nullable=False, server_default=""))
        batch_op.add_column(
            sa.Column(
                "realizer_version",
                sa.String(length=64),
                nullable=False,
                server_default="eidolon.persona_realizer",
            )
        )
        batch_op.add_column(sa.Column("applied_event_id", sa.String(length=64), nullable=True))
        batch_op.create_index("ix_persona_genomes_schema_version", ["schema_version"])
        batch_op.create_index("ix_persona_genomes_genome_hash", ["genome_hash"])
        batch_op.create_index("ix_persona_genomes_applied_event_id", ["applied_event_id"])
        batch_op.drop_column("prompt_markdown")
        batch_op.drop_column("evolution_state_json")
        batch_op.create_check_constraint(
            "schema_current",
            "schema_version = 'eidolon.persona_genome'",
        )
        batch_op.create_check_constraint(
            "realizer_current",
            "realizer_version = 'eidolon.persona_realizer'",
        )

    with op.batch_alter_table("events") as batch_op:
        batch_op.add_column(sa.Column("companion_id", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("event_class", sa.String(length=8), nullable=False, server_default="audit")
        )
        batch_op.add_column(sa.Column("source", sa.String(length=16), nullable=False, server_default="data"))
        batch_op.add_column(sa.Column("severity", sa.String(length=8), nullable=False, server_default="info"))
        batch_op.add_column(sa.Column("outcome", sa.String(length=12), nullable=False, server_default="success"))
        batch_op.add_column(sa.Column("reason", sa.String(length=256), nullable=True))
        batch_op.add_column(sa.Column("trace_id", sa.String(length=64), nullable=True))
        batch_op.add_column(
            sa.Column("data_classification", sa.String(length=10), nullable=False, server_default="safe")
        )
        batch_op.add_column(sa.Column("schema_version", sa.Integer(), nullable=False, server_default="1"))
        batch_op.add_column(sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index("ix_events_companion_id", ["companion_id"])
        batch_op.create_index("ix_events_event_class", ["event_class"])
        batch_op.create_index("ix_events_source", ["source"])
        batch_op.create_index("ix_events_severity", ["severity"])
        batch_op.create_index("ix_events_outcome", ["outcome"])
        batch_op.create_index("ix_events_trace_id", ["trace_id"])
        batch_op.create_index("ix_events_owner_created", ["owner_id", "created_at"])
    op.execute("UPDATE events SET occurred_at = created_at WHERE occurred_at IS NULL")


def downgrade() -> None:
    with op.batch_alter_table("events") as batch_op:
        for index in (
            "ix_events_owner_created",
            "ix_events_trace_id",
            "ix_events_outcome",
            "ix_events_severity",
            "ix_events_source",
            "ix_events_event_class",
            "ix_events_companion_id",
        ):
            batch_op.drop_index(index)
        for column in (
            "occurred_at",
            "schema_version",
            "data_classification",
            "trace_id",
            "reason",
            "outcome",
            "severity",
            "source",
            "event_class",
            "companion_id",
        ):
            batch_op.drop_column(column)

    with op.batch_alter_table("persona_genomes") as batch_op:
        batch_op.drop_constraint("ck_persona_genomes_realizer_current", type_="check")
        batch_op.drop_constraint("ck_persona_genomes_schema_current", type_="check")
        batch_op.add_column(sa.Column("evolution_state_json", sa.JSON(), nullable=False, server_default="{}"))
        batch_op.add_column(sa.Column("prompt_markdown", sa.Text(), nullable=False, server_default=""))
        for index in (
            "ix_persona_genomes_applied_event_id",
            "ix_persona_genomes_genome_hash",
            "ix_persona_genomes_schema_version",
        ):
            batch_op.drop_index(index)
        for column in ("applied_event_id", "realizer_version", "genome_hash", "schema_version"):
            batch_op.drop_column(column)

    with op.batch_alter_table("companions") as batch_op:
        batch_op.drop_index("ix_companions_companion_type")
        batch_op.drop_column("companion_type")
