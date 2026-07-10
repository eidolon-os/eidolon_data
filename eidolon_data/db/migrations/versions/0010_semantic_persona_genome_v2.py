"""Replace legacy persona storage with semantic immutable v2 snapshots.

Revision ID: 0010_semantic_persona_genome_v2
Revises: 0009_persona_genome_v1

Legacy persona content is deliberately discarded. There is no reliable semantic
mapping from the removed compiler DSL to PersonaGenome.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0010_semantic_persona_genome_v2"
down_revision = "0009_persona_genome_v1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing_columns = {
        column["name"]
        for column in sa.inspect(op.get_bind()).get_columns("persona_genomes")
    }
    with op.batch_alter_table("persona_genomes") as batch_op:
        batch_op.drop_index("ix_persona_genomes_genome_hash")
        batch_op.drop_index("ix_persona_genomes_stable_prompt_hash")
        if "realizer_version" in existing_columns:
            batch_op.drop_column("compiler_version")
        else:
            batch_op.alter_column(
                "compiler_version",
                new_column_name="realizer_version",
                existing_type=sa.String(length=64),
                nullable=False,
            )
        batch_op.drop_column("stable_prompt_hash")
        batch_op.create_index("ix_persona_genomes_genome_hash", ["genome_hash"], unique=False)

    # v1 compiler-oriented data is not semantically compatible with v2.
    op.execute("UPDATE companions SET current_genome_id = NULL")
    op.execute("DELETE FROM persona_genomes")

    with op.batch_alter_table("persona_genomes") as batch_op:
        batch_op.create_foreign_key(
            "fk_persona_genomes_base_genome",
            "persona_genomes",
            ["base_genome_id"],
            ["genome_id"],
            ondelete="SET NULL",
        )

    with op.batch_alter_table("companions") as batch_op:
        batch_op.create_foreign_key(
            "fk_companions_current_genome",
            "persona_genomes",
            ["current_genome_id"],
            ["genome_id"],
            ondelete="SET NULL",
        )
        batch_op.create_foreign_key(
            "fk_companions_default_memory_realm",
            "memory_realms",
            ["default_memory_realm_id"],
            ["realm_id"],
            ondelete="SET NULL",
        )

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


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_persona_genomes_immutable")
    with op.batch_alter_table("companions") as batch_op:
        batch_op.drop_constraint("fk_companions_default_memory_realm", type_="foreignkey")
        batch_op.drop_constraint("fk_companions_current_genome", type_="foreignkey")
    with op.batch_alter_table("persona_genomes") as batch_op:
        batch_op.drop_constraint("fk_persona_genomes_base_genome", type_="foreignkey")
        batch_op.drop_index("ix_persona_genomes_genome_hash")
        batch_op.add_column(sa.Column("stable_prompt_hash", sa.String(length=80), nullable=True))
        batch_op.alter_column(
            "realizer_version",
            new_column_name="compiler_version",
            existing_type=sa.String(length=64),
            nullable=False,
        )
        batch_op.create_index("ix_persona_genomes_genome_hash", ["genome_hash"], unique=True)
        batch_op.create_index(
            "ix_persona_genomes_stable_prompt_hash", ["stable_prompt_hash"], unique=False
        )
