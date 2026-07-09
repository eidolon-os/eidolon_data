"""Collapse persona genomes to v1 JSON snapshots."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0009_persona_genome_v1"
down_revision = "0008_companion_type"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("persona_genomes") as batch_op:
        batch_op.add_column(
            sa.Column(
                "schema_version",
                sa.String(length=64),
                nullable=False,
                server_default="eidolon.persona_genome.v1",
            )
        )
        batch_op.add_column(sa.Column("genome_hash", sa.String(length=80), nullable=True))
        batch_op.add_column(
            sa.Column(
                "compiler_version",
                sa.String(length=64),
                nullable=False,
                server_default="eidolon.persona_compiler.v1",
            )
        )
        batch_op.add_column(sa.Column("stable_prompt_hash", sa.String(length=80), nullable=True))
        batch_op.add_column(sa.Column("applied_event_id", sa.String(length=64), nullable=True))

    op.execute("UPDATE persona_genomes SET genome_hash = 'legacy_' || genome_id")

    with op.batch_alter_table("persona_genomes") as batch_op:
        batch_op.alter_column("genome_hash", nullable=False)
        batch_op.create_index("ix_persona_genomes_schema_version", ["schema_version"])
        batch_op.create_index("ix_persona_genomes_genome_hash", ["genome_hash"], unique=True)
        batch_op.create_index("ix_persona_genomes_stable_prompt_hash", ["stable_prompt_hash"])
        batch_op.create_index("ix_persona_genomes_applied_event_id", ["applied_event_id"])
        batch_op.drop_column("prompt_markdown")
        batch_op.drop_column("evolution_state_json")


def downgrade() -> None:
    with op.batch_alter_table("persona_genomes") as batch_op:
        batch_op.add_column(sa.Column("prompt_markdown", sa.Text(), nullable=False, server_default=""))
        batch_op.add_column(sa.Column("evolution_state_json", sa.JSON(), nullable=False, server_default="{}"))
        batch_op.drop_index("ix_persona_genomes_applied_event_id")
        batch_op.drop_index("ix_persona_genomes_stable_prompt_hash")
        batch_op.drop_index("ix_persona_genomes_genome_hash")
        batch_op.drop_index("ix_persona_genomes_schema_version")
        batch_op.drop_column("applied_event_id")
        batch_op.drop_column("stable_prompt_hash")
        batch_op.drop_column("compiler_version")
        batch_op.drop_column("genome_hash")
        batch_op.drop_column("schema_version")
