"""Delete the retired ``companion_voice`` snapshot from Companion runtime config.

0005 kept it as evidence that the old voice selector could be removed. Nothing
reads it; the Companion's voice choice is now ``speech_voice``. Only that key
is removed and only changed rows are written, so running it twice is harmless.
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0026_retire_companion_voice"
down_revision = "0025_team_snapshots"
branch_labels = None
depends_on = None


def upgrade():
    table = sa.table(
        "companions",
        sa.column("companion_id", sa.String),
        sa.column("runtime_config_json", sa.JSON),
    )
    connection = op.get_bind()
    for row in connection.execute(sa.select(table)).mappings():
        config = row["runtime_config_json"]
        if isinstance(config, str):
            config = json.loads(config)
        if not isinstance(config, dict) or "companion_voice" not in config:
            continue
        config = {key: value for key, value in config.items() if key != "companion_voice"}
        connection.execute(
            table.update()
            .where(table.c.companion_id == row["companion_id"])
            .values(runtime_config_json=config)
        )


def downgrade():
    # A data clean-up, not a schema change: there is nothing to put back.
    pass
