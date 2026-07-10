"""Align persona database defaults with the canonical contract.

Revision ID: 0015_persona_canonical_defaults
Revises: 0014_canonical_persona_schema
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0015_persona_canonical_defaults"
down_revision = "0014_canonical_persona_schema"
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
    op.execute("DROP TRIGGER IF EXISTS trg_persona_genomes_immutable")
    with op.batch_alter_table("persona_genomes") as batch_op:
        batch_op.alter_column(
            "schema_version",
            existing_type=sa.String(length=64),
            nullable=False,
            server_default="eidolon.persona_genome",
        )
        batch_op.alter_column(
            "realizer_version",
            existing_type=sa.String(length=64),
            nullable=False,
            server_default="eidolon.persona_realizer",
        )
    _create_immutable_trigger()


def downgrade() -> None:
    raise RuntimeError("canonical persona defaults migration is irreversible")
