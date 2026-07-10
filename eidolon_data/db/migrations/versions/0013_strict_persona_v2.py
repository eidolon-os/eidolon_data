"""Enforce the only supported persona schema and realizer.

Revision ID: 0013_strict_persona_v2
Revises: 0012_persona_applied_event
"""

from __future__ import annotations

from alembic import op

revision = "0013_strict_persona_v2"
down_revision = "0012_persona_applied_event"
branch_labels = None
depends_on = None


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


def upgrade() -> None:
    with op.batch_alter_table("persona_genomes") as batch_op:
        batch_op.create_check_constraint(
            "schema_v2",
            "schema_version = 'eidolon.persona_genome.v2'",
        )
        batch_op.create_check_constraint(
            "realizer_v1",
            "realizer_version = 'eidolon.persona_realizer.v1'",
        )
    _create_immutable_trigger()


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_persona_genomes_immutable")
    with op.batch_alter_table("persona_genomes") as batch_op:
        batch_op.drop_constraint("ck_persona_genomes_realizer_v1", type_="check")
        batch_op.drop_constraint("ck_persona_genomes_schema_v2", type_="check")
    _create_immutable_trigger()
