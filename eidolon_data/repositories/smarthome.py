"""Read queries for the Owner's smart-home registry.

Rows go in and an SDK ``Registry`` comes out: the registry's shape and its
validity are the SDK's contract, and nothing outside this authority sees a row.
"""

from __future__ import annotations

from eidolon_sdk.biz.smarthome import Area, Command, Device, Placement, Registry, Scene
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eidolon_data.repositories.base import Repository
from eidolon_data.schema import (
    SmartHomeAreaRow,
    SmartHomeDeviceRow,
    SmartHomePlacementRow,
    SmartHomeRegistryRow,
    SmartHomeSceneActionRow,
    SmartHomeSceneRow,
)


class SmartHomeRegistryRepository(Repository):
    async def get(self, owner_id: str) -> Registry:
        """This Owner's registry; revision 0 and empty when nothing was ever written."""

        async with self._session_factory() as session:
            # The revision is read before the rows it labels. A read that races
            # a write can then only pair new rows with the older revision, which
            # a caller corrects on its next read; the other order would label
            # old rows with the new revision, and a caller comparing revisions
            # would keep them forever.
            revision = await session.scalar(
                select(SmartHomeRegistryRow.revision).where(
                    SmartHomeRegistryRow.owner_id == owner_id
                )
            )
            return await load_registry(session, owner_id, revision=revision or 0)


async def load_registry(session: AsyncSession, owner_id: str, *, revision: int) -> Registry:
    """Build this Owner's registry from its rows, labelled with ``revision``."""

    areas = await session.scalars(
        select(SmartHomeAreaRow)
        .where(SmartHomeAreaRow.owner_id == owner_id)
        .order_by(SmartHomeAreaRow.sort_order, SmartHomeAreaRow.position)
    )
    devices = await session.scalars(
        select(SmartHomeDeviceRow)
        .where(SmartHomeDeviceRow.owner_id == owner_id)
        .order_by(SmartHomeDeviceRow.position)
    )
    scenes = list(
        await session.scalars(
            select(SmartHomeSceneRow)
            .where(SmartHomeSceneRow.owner_id == owner_id)
            .order_by(SmartHomeSceneRow.position)
        )
    )
    actions: dict[str, list[Command]] = {scene.scene_id: [] for scene in scenes}
    for action in await session.scalars(
        select(SmartHomeSceneActionRow)
        .where(SmartHomeSceneActionRow.owner_id == owner_id)
        .order_by(SmartHomeSceneActionRow.scene_id, SmartHomeSceneActionRow.position)
    ):
        actions[action.scene_id].append(
            Command(
                device_id=action.device_id,
                trait=action.trait,
                command=action.command,
                params=dict(action.params_json),
            )
        )
    placements = await session.scalars(
        select(SmartHomePlacementRow)
        .where(SmartHomePlacementRow.owner_id == owner_id)
        .order_by(SmartHomePlacementRow.device_ref)
    )
    return Registry(
        revision=revision,
        areas=tuple(
            Area(area_id=row.area_id, name=row.name, order=row.sort_order) for row in areas
        ),
        devices=tuple(
            Device(
                device_id=row.device_id,
                name=row.name,
                aliases=tuple(row.aliases_json),
                type=row.device_type,
                area_id=row.area_id,
                provider=row.provider,
                provider_ref=row.provider_ref,
            )
            for row in devices
        ),
        scenes=tuple(
            Scene(scene_id=row.scene_id, name=row.name, actions=tuple(actions[row.scene_id]))
            for row in scenes
        ),
        placements=tuple(
            Placement(device_ref=row.device_ref, area_id=row.area_id) for row in placements
        ),
    )
