"""Align persona genome database defaults with semantic v2.

Revision ID: 0011_persona_v2_defaults
Revises: 0010_semantic_persona_genome_v2
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0011_persona_v2_defaults"
down_revision = "0010_semantic_persona_genome_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("persona_genomes") as batch_op:
        batch_op.alter_column(
            "schema_version",
            existing_type=sa.String(length=64),
            nullable=False,
            server_default="eidolon.persona_genome",
        )
    _create_immutable_trigger()


def downgrade() -> None:
    with op.batch_alter_table("persona_genomes") as batch_op:
        batch_op.alter_column(
            "schema_version",
            existing_type=sa.String(length=64),
            nullable=False,
            server_default="eidolon.persona_genome.v1",
        )
    _create_immutable_trigger()


def _create_immutable_trigger() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_persona_genomes_immutable")
    op.execute(
        """
        CREATE TRIGGER trg_persona_genomes_immutable
        BEFORE UPDATE OF companion_id, version, base_genome_id, schema_version,
                         genome_hash, realizer_version, genome_json
        ON persona_genomes
        BEGIN
            SELECT RAISE(ABORT, 'persona genome snapshots are immutable');
        END
        """
    )
