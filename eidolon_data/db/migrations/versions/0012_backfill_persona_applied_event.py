"""Backfill committed persona genome audit pointers.

Revision ID: 0012_persona_applied_event
Revises: 0011_persona_v2_defaults
"""

from __future__ import annotations

from alembic import op

revision = "0012_persona_applied_event"
down_revision = "0011_persona_v2_defaults"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE persona_genomes
        SET applied_event_id = (
            SELECT events.event_id
            FROM events
            WHERE events.subject_type = 'persona_genome'
              AND events.subject_id = persona_genomes.genome_id
              AND events.event_type = 'persona.genome.committed'
            ORDER BY events.occurred_at DESC, events.event_id DESC
            LIMIT 1
        )
        WHERE persona_genomes.status = 'committed'
          AND persona_genomes.applied_event_id IS NULL
          AND EXISTS (
              SELECT 1
              FROM events
              WHERE events.subject_type = 'persona_genome'
                AND events.subject_id = persona_genomes.genome_id
                AND events.event_type = 'persona.genome.committed'
          )
        """
    )


def downgrade() -> None:
    # The backfilled value is valid audit data and cannot be distinguished from
    # pointers written by the application, so downgrading keeps it intact.
    pass
