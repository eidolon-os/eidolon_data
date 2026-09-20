"""Persist the initial official display artwork independently of persona.

Only exact published preset revisions on the first genome qualify. Existing
visual choices and custom/unknown templates are untouched. Runtime reads never
infer appearance from names or the currently active personality.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0002_companion_artwork"
down_revision = "0001_system_data_v2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT c.companion_id, c.profile_json, g.genome_json "
            "FROM companions c JOIN persona_genomes g ON g.companion_id=c.companion_id "
            "WHERE g.version=1"
        )
    ).mappings()
    table = sa.table(
        "companions", sa.column("companion_id", sa.String), sa.column("profile_json", sa.JSON)
    )
    for row in rows:
        profile = row["profile_json"]
        genome = row["genome_json"]
        if isinstance(profile, str):
            profile = json.loads(profile)
        if isinstance(genome, str):
            genome = json.loads(genome)
        if not isinstance(profile, dict) or "artwork_id" in profile:
            continue
        provenance = genome.get("provenance", {})
        preset = provenance.get("source_preset_id")
        if provenance.get("source_preset_revision") != "1" or preset not in {
            "metal",
            "wood",
            "water",
            "fire",
            "earth",
        }:
            continue
        connection.execute(
            table.update()
            .where(table.c.companion_id == row["companion_id"])
            .values(profile_json={**profile, "artwork_id": f"five-elements/1/{preset}"})
        )


def downgrade() -> None:
    # Additive presentation metadata remains valid for old code, which ignores
    # it. Never erase a persisted visual choice during a software rollback.
    pass
