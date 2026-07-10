"""Make the canonical persona contract the only supported schema.

Existing persona snapshots are intentionally discarded. The project does not
attempt to infer canonical semantics from an older stored representation.

Revision ID: 0014_canonical_persona_schema
Revises: 0013_strict_persona_v2
"""

from __future__ import annotations

from alembic import op

revision = "0014_canonical_persona_schema"
down_revision = "0013_strict_persona_v2"
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
    op.execute("UPDATE companions SET current_genome_id = NULL")
    op.execute("DELETE FROM persona_genomes")
    op.execute("DROP TRIGGER IF EXISTS trg_persona_genomes_immutable")
    with op.batch_alter_table("persona_genomes") as batch_op:
        batch_op.drop_constraint("schema_v2", type_="check")
        batch_op.drop_constraint("realizer_v1", type_="check")
        batch_op.create_check_constraint(
            "schema_current",
            "schema_version = 'eidolon.persona_genome'",
        )
        batch_op.create_check_constraint(
            "realizer_current",
            "realizer_version = 'eidolon.persona_realizer'",
        )
    _create_immutable_trigger()


def downgrade() -> None:
    raise RuntimeError("canonical persona schema migration is irreversible")
