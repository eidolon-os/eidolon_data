"""Persistence rows for the Owner's smart-home registry.

The registry — areas, smart-home devices, scenes, and which area each Eidolon
device stands in — is Owner master data. Its contract, including what makes a
registry valid, is ``eidolon_sdk.biz.smarthome``; these rows only store it.
A device's current state is not here: it belongs to the Provider that
implements the device.

Every row carries ``owner_id`` and every reference between rows is a composite
key that includes it, so a row can never point at another Owner's row. The
references between registry rows deliberately do not cascade: removing an area
that devices still stand in, or a device a scene still drives, is refused
rather than silently taking the dependants with it.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql.expression import false as sa_false

from eidolon_data.db.base import Base, utc_now

JsonDict = dict[str, Any]
JsonList = list[Any]


class SmartHomeRegistryRow(Base):
    """The Owner's registry revision; absent until the first write.

    One counter for the whole registry rather than one per row, because what a
    reader caches and a writer compares against is the home as a whole: a panel
    that holds revision 7 holds all of revision 7.
    """

    __tablename__ = "smarthome_registries"

    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), primary_key=True
    )
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (CheckConstraint("revision > 0", name="smarthome_registry_revision_positive"),)


class SmartHomeAreaRow(Base):
    __tablename__ = "smarthome_areas"

    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), primary_key=True
    )
    area_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(32))
    #: The SDK's ``Area.order``: where the Owner wants it shown.
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    #: Insertion order, which breaks ties in ``sort_order`` and keeps a read
    #: stable.
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_smarthome_areas_owner_name"),)


class SmartHomeDeviceRow(Base):
    __tablename__ = "smarthome_devices"

    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), primary_key=True
    )
    device_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(32))
    aliases_json: Mapped[JsonList] = mapped_column(default=list)
    #: An SDK ``DeviceType``. No check constraint: the vocabulary is the SDK's,
    #: and a second copy of it here would drift from the first.
    device_type: Mapped[str] = mapped_column(String(32))
    area_id: Mapped[str | None] = mapped_column(String(128))
    provider: Mapped[str] = mapped_column(String(128), default="virtual")
    provider_ref: Mapped[str | None] = mapped_column(String(128))
    #: v2 (2026-10-02): what the device really supports and where it came from.
    #: ``None`` traits mean the type's preset; the vocabulary stays the SDK's.
    traits_json: Mapped[JsonList | None] = mapped_column(nullable=True)
    limits_json: Mapped[JsonDict | None] = mapped_column(nullable=True)
    source: Mapped[str] = mapped_column(String(16), default="manual", server_default="manual")
    overrides_json: Mapped[JsonList] = mapped_column(default=list)
    synced_at_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    orphaned: Mapped[bool] = mapped_column(Boolean, default=False, server_default=sa_false())
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        # No ondelete: an area with devices in it cannot be removed.
        ForeignKeyConstraint(
            ["owner_id", "area_id"],
            ["smarthome_areas.owner_id", "smarthome_areas.area_id"],
            name="fk_smarthome_devices_owner_area",
        ),
        Index("ix_smarthome_devices_owner_area", "owner_id", "area_id"),
    )


class SmartHomeSceneRow(Base):
    __tablename__ = "smarthome_scenes"

    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), primary_key=True
    )
    scene_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(32))
    #: v2: a scene a Provider runs as a whole has no action rows of its own.
    provider: Mapped[str | None] = mapped_column(String(128), nullable=True)
    provider_ref: Mapped[str | None] = mapped_column(String(128), nullable=True)
    position: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)


class SmartHomeSceneActionRow(Base):
    """One command of a scene, in the order the scene runs them.

    Part of the scene, so it goes with the scene. It also references a device,
    and that reference does not cascade: a device a scene still drives cannot
    be removed.
    """

    __tablename__ = "smarthome_scene_actions"

    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), primary_key=True
    )
    scene_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    device_id: Mapped[str] = mapped_column(String(128))
    trait: Mapped[str] = mapped_column(String(32))
    command: Mapped[str] = mapped_column(String(128))
    params_json: Mapped[JsonDict] = mapped_column(default=dict)

    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "scene_id"],
            ["smarthome_scenes.owner_id", "smarthome_scenes.scene_id"],
            name="fk_smarthome_scene_actions_owner_scene",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["owner_id", "device_id"],
            ["smarthome_devices.owner_id", "smarthome_devices.device_id"],
            name="fk_smarthome_scene_actions_owner_device",
        ),
        Index("ix_smarthome_scene_actions_owner_device", "owner_id", "device_id"),
    )


class SmartHomePlacementRow(Base):
    """Which area an Eidolon device (a panel, a BOX-3) stands in.

    ``device_ref`` is the Hub device instance id (``DeviceRef.device_instance_id``)
    and has no foreign key: Hub owns device admission. What this authority owns
    is only the Owner's statement of where the device stands.
    """

    __tablename__ = "smarthome_placements"

    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), primary_key=True
    )
    device_ref: Mapped[str] = mapped_column(String(128), primary_key=True)
    area_id: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        # No ondelete: an area an Eidolon device stands in cannot be removed.
        ForeignKeyConstraint(
            ["owner_id", "area_id"],
            ["smarthome_areas.owner_id", "smarthome_areas.area_id"],
            name="fk_smarthome_placements_owner_area",
        ),
        Index("ix_smarthome_placements_owner_area", "owner_id", "area_id"),
    )
