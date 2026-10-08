"""Separate the retired voice selector from conversational policy.

A September 17 development build persisted both a selector in preferences and
an independent voice snapshot. Remove only the redundant selector when that
snapshot proves its value is preserved. Never guess a voice or discard unknown
preferences; conflicting/incomplete data requires explicit resolution.
"""

from __future__ import annotations

import json

import sqlalchemy as sa
from alembic import op

revision = "0005_conversation_preferences"
down_revision = "0004_smarthome_v2_columns"
branch_labels = None
depends_on = None


def upgrade() -> None:
    connection = op.get_bind()
    table = sa.table(
        "companions",
        sa.column("companion_id", sa.String),
        sa.column("runtime_config_json", sa.JSON),
    )
    updates = []
    for row in connection.execute(sa.select(table)).mappings():
        config = row["runtime_config_json"]
        if isinstance(config, str):
            config = json.loads(config)
        config = dict(config)
        preferences = dict(config.get("conversation_preferences", {}))
        if "voice_profile_id" in preferences:
            voice_id = preferences["voice_profile_id"]
            voice = config.get("companion_voice")
            if (
                not isinstance(voice_id, str)
                or not voice_id.strip()
                or not isinstance(voice, dict)
                or voice.get("profile_id") != voice_id
            ):
                raise ValueError(f"{row['companion_id']}: voice selector has no matching snapshot")
            del preferences["voice_profile_id"]
            config["conversation_preferences"] = preferences
            updates.append((row["companion_id"], config))
        # Validate every row before writing any: unknown meaningful fields
        # must not be silently projected away during migration.
        # Freeze the policy vocabulary of this revision. Historical replay
        # must not depend on how the live SDK contract evolves later.
        allowed = {
            "response_length": ("brief", "balanced", "detailed"),
            "advice": ("when_asked", "proactive"),
            "follow_up": ("when_needed", "conversational"),
        }
        if any(
            key not in allowed or value not in allowed[key] for key, value in preferences.items()
        ):
            raise ValueError(f"{row['companion_id']}: unsupported conversation preferences")
    for companion_id, config in updates:
        connection.execute(
            table.update()
            .where(table.c.companion_id == companion_id)
            .values(runtime_config_json=config)
        )


def downgrade() -> None:
    # The independent snapshot survives; do not reintroduce invalid policy.
    pass
