"""Add the Owner's smart-home registry.

Areas, smart-home devices, scenes and their actions, where Eidolon devices
stand, and one registry revision per Owner. Purely additive: no existing table
or row changes. References between registry rows are composite keys that
include ``owner_id`` and do not cascade, so the schema itself refuses a
cross-Owner reference and the silent removal of an area or device that is still
in use.

Revision ID: 0003_smarthome_registry
Revises: 0002_companion_artwork
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0003_smarthome_registry"
down_revision = "0002_companion_artwork"
branch_labels = None
depends_on = None

NOW = sa.text("CURRENT_TIMESTAMP")
EMPTY_JSON = sa.text("'{}'")
EMPTY_JSON_LIST = sa.text("'[]'")


def _owner_key() -> sa.Column:
    return sa.Column("owner_id", sa.String(64), primary_key=True)


def _owner_fk() -> sa.ForeignKeyConstraint:
    return sa.ForeignKeyConstraint(["owner_id"], ["owners.owner_id"], ondelete="CASCADE")


def _timestamps() -> tuple[sa.Column, sa.Column]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=NOW),
    )


def upgrade() -> None:
    op.create_table(
        "smarthome_registries",
        _owner_key(),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        *_timestamps(),
        _owner_fk(),
        sa.CheckConstraint("revision > 0", name="smarthome_registry_revision_positive"),
    )

    op.create_table(
        "smarthome_areas",
        _owner_key(),
        sa.Column("area_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("position", sa.Integer(), nullable=False),
        *_timestamps(),
        _owner_fk(),
        sa.UniqueConstraint("owner_id", "name", name="uq_smarthome_areas_owner_name"),
    )

    op.create_table(
        "smarthome_devices",
        _owner_key(),
        sa.Column("device_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("aliases_json", sa.JSON(), nullable=False, server_default=EMPTY_JSON_LIST),
        sa.Column("device_type", sa.String(32), nullable=False),
        sa.Column("area_id", sa.String(128)),
        sa.Column("provider", sa.String(128), nullable=False, server_default="virtual"),
        sa.Column("provider_ref", sa.String(128)),
        sa.Column("position", sa.Integer(), nullable=False),
        *_timestamps(),
        _owner_fk(),
        sa.ForeignKeyConstraint(
            ["owner_id", "area_id"],
            ["smarthome_areas.owner_id", "smarthome_areas.area_id"],
            name="fk_smarthome_devices_owner_area",
        ),
    )
    op.create_index("ix_smarthome_devices_owner_area", "smarthome_devices", ["owner_id", "area_id"])

    op.create_table(
        "smarthome_scenes",
        _owner_key(),
        sa.Column("scene_id", sa.String(128), primary_key=True),
        sa.Column("name", sa.String(32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        *_timestamps(),
        _owner_fk(),
    )

    op.create_table(
        "smarthome_scene_actions",
        _owner_key(),
        sa.Column("scene_id", sa.String(128), primary_key=True),
        sa.Column("position", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.String(128), nullable=False),
        sa.Column("trait", sa.String(32), nullable=False),
        sa.Column("command", sa.String(128), nullable=False),
        sa.Column("params_json", sa.JSON(), nullable=False, server_default=EMPTY_JSON),
        _owner_fk(),
        sa.ForeignKeyConstraint(
            ["owner_id", "scene_id"],
            ["smarthome_scenes.owner_id", "smarthome_scenes.scene_id"],
            name="fk_smarthome_scene_actions_owner_scene",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id", "device_id"],
            ["smarthome_devices.owner_id", "smarthome_devices.device_id"],
            name="fk_smarthome_scene_actions_owner_device",
        ),
    )
    op.create_index(
        "ix_smarthome_scene_actions_owner_device",
        "smarthome_scene_actions",
        ["owner_id", "device_id"],
    )

    op.create_table(
        "smarthome_placements",
        _owner_key(),
        sa.Column("device_ref", sa.String(128), primary_key=True),
        sa.Column("area_id", sa.String(128), nullable=False),
        *_timestamps(),
        _owner_fk(),
        sa.ForeignKeyConstraint(
            ["owner_id", "area_id"],
            ["smarthome_areas.owner_id", "smarthome_areas.area_id"],
            name="fk_smarthome_placements_owner_area",
        ),
    )
    op.create_index(
        "ix_smarthome_placements_owner_area", "smarthome_placements", ["owner_id", "area_id"]
    )


def downgrade() -> None:
    for table in (
        "smarthome_placements",
        "smarthome_scene_actions",
        "smarthome_scenes",
        "smarthome_devices",
        "smarthome_areas",
        "smarthome_registries",
    ):
        op.drop_table(table)
