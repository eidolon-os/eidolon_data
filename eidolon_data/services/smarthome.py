"""Transactional commands for the Owner's smart-home registry.

Every write names the registry revision its caller last read and moves it by
exactly one, in the same transaction as the change and its governance fact.
What a valid registry is stays the SDK's decision: each command builds the
registry it would produce and lets ``eidolon_sdk.biz.smarthome.Registry``
accept or refuse it. The two refusals that are about consequences rather than
shape — an area still in use, a device a scene still drives — are checked here
and are never resolved by quietly removing the dependants.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from eidolon_sdk.biz.smarthome import (
    REGISTRY_LIMIT_EXCEEDED,
    Area,
    Device,
    Placement,
    Registry,
    RegistryError,
    Scene,
    SmartHomeError,
)
from eidolon_sdk.biz.smarthome.samples import SAMPLES
from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from eidolon_data.audit import governance_fact
from eidolon_data.db.base import Base, utc_now
from eidolon_data.repositories.smarthome import SmartHomeRegistryRepository, load_registry
from eidolon_data.schema import (
    OwnerRow,
    SmartHomeAreaRow,
    SmartHomeDeviceRow,
    SmartHomePlacementRow,
    SmartHomeRegistryRow,
    SmartHomeSceneActionRow,
    SmartHomeSceneRow,
)

AREA_NOT_EMPTY = "AREA_NOT_EMPTY"
DEVICE_IN_SCENE = "DEVICE_IN_SCENE"
REGISTRY_NOT_EMPTY = "REGISTRY_NOT_EMPTY"
REVISION_CONFLICT = "REVISION_CONFLICT"
NOT_FOUND = "NOT_FOUND"


class SmartHomeRegistryError(ValueError):
    """A registry command this authority refused, and why in a word."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class SmartHomeRegistryNotFound(SmartHomeRegistryError):
    """The Owner, or the thing the command names, is not in this Owner's registry.

    Another Owner's area is answered exactly like one that does not exist.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, code=NOT_FOUND)


class SmartHomeRegistryConflict(SmartHomeRegistryError):
    """The caller's revision is not the registry's current one; re-read first."""

    def __init__(self, *, expected_revision: int, current_revision: int) -> None:
        super().__init__(
            f"smart home registry revision conflict: expected {expected_revision}, "
            f"current {current_revision}",
            code=REVISION_CONFLICT,
        )
        self.current_revision = current_revision


class SmartHomeRegistryRefused(SmartHomeRegistryError):
    """The command is well formed, but the registry as it stands refuses it."""


class SmartHomeRegistryInvalid(SmartHomeRegistryError):
    """The registry the command would produce fails the SDK contract.

    ``code`` is always the SDK's: a registry code (``DEVICE_AREA_UNKNOWN``,
    ``DUPLICATE_DEVICE_NAME_IN_AREA``, ``LIMIT_EXCEEDED`` for a collection past
    its ``MAX_*`` cap…) or, for a scene step, a command code
    (``UNSUPPORTED_COMMAND``, ``OUT_OF_RANGE``…).
    """


class SmartHomeRegistryService:
    """Commands for one Owner's areas, devices, scenes, and placements.

    Every write returns the registry it produced, so the caller has the new
    revision without a second read.
    """

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def get_registry(self, owner_id: str) -> Registry:
        return await SmartHomeRegistryRepository(self._session_factory).get(owner_id)

    # --- Areas ------------------------------------------------------------

    async def create_area(self, *, owner_id: str, area: Area, expected_revision: int) -> Registry:
        async with self._change(owner_id, expected_revision, "area.created", area.area_id) as c:
            c.propose(areas=(*c.current.areas, area))
            c.session.add(
                SmartHomeAreaRow(
                    owner_id=owner_id,
                    area_id=area.area_id,
                    name=area.name,
                    sort_order=area.order,
                    position=await c.next_position(SmartHomeAreaRow),
                )
            )
        return c.registry

    async def update_area(self, *, owner_id: str, area: Area, expected_revision: int) -> Registry:
        async with self._change(owner_id, expected_revision, "area.updated", area.area_id) as c:
            row = await c.session.get(SmartHomeAreaRow, (owner_id, area.area_id))
            if row is None:
                raise SmartHomeRegistryNotFound("area not found for owner")
            c.propose(areas=_replace(c.current.areas, "area_id", area))
            row.name = area.name
            row.sort_order = area.order
            row.updated_at = utc_now()
        return c.registry

    async def delete_area(self, *, owner_id: str, area_id: str, expected_revision: int) -> Registry:
        async with self._change(owner_id, expected_revision, "area.deleted", area_id) as c:
            row = await c.session.get(SmartHomeAreaRow, (owner_id, area_id))
            if row is None:
                raise SmartHomeRegistryNotFound("area not found for owner")
            devices = [d.device_id for d in c.current.devices if d.area_id == area_id]
            placed = [p.device_ref for p in c.current.placements if p.area_id == area_id]
            if devices or placed:
                raise SmartHomeRegistryRefused(
                    f"area {area_id!r} still has {len(devices)} device(s) and "
                    f"{len(placed)} placement(s); move or remove them first",
                    code=AREA_NOT_EMPTY,
                )
            c.propose(areas=_without(c.current.areas, "area_id", area_id))
            await c.session.delete(row)
        return c.registry

    # --- Devices ----------------------------------------------------------

    async def create_device(
        self, *, owner_id: str, device: Device, expected_revision: int
    ) -> Registry:
        async with self._change(
            owner_id, expected_revision, "device.created", device.device_id
        ) as c:
            c.propose(devices=(*c.current.devices, device))
            c.session.add(
                SmartHomeDeviceRow(
                    owner_id=owner_id,
                    device_id=device.device_id,
                    position=await c.next_position(SmartHomeDeviceRow),
                    **_device_columns(device),
                )
            )
        return c.registry

    async def update_device(
        self, *, owner_id: str, device: Device, expected_revision: int
    ) -> Registry:
        async with self._change(
            owner_id, expected_revision, "device.updated", device.device_id
        ) as c:
            row = await c.session.get(SmartHomeDeviceRow, (owner_id, device.device_id))
            if row is None:
                raise SmartHomeRegistryNotFound("device not found for owner")
            # A new type is validated against every scene that drives the
            # device, so a light cannot become a lock under a "set level" step.
            c.propose(devices=_replace(c.current.devices, "device_id", device))
            for column, value in _device_columns(device).items():
                setattr(row, column, value)
            row.updated_at = utc_now()
        return c.registry

    async def delete_device(
        self, *, owner_id: str, device_id: str, expected_revision: int
    ) -> Registry:
        async with self._change(owner_id, expected_revision, "device.deleted", device_id) as c:
            row = await c.session.get(SmartHomeDeviceRow, (owner_id, device_id))
            if row is None:
                raise SmartHomeRegistryNotFound("device not found for owner")
            scenes = [
                s.scene_id
                for s in c.current.scenes
                if any(action.device_id == device_id for action in s.actions)
            ]
            if scenes:
                raise SmartHomeRegistryRefused(
                    f"device {device_id!r} is used by scene(s) {', '.join(scenes)}; "
                    "remove it from them first",
                    code=DEVICE_IN_SCENE,
                )
            c.propose(devices=_without(c.current.devices, "device_id", device_id))
            await c.session.delete(row)
        return c.registry

    # --- Scenes -----------------------------------------------------------

    async def create_scene(
        self, *, owner_id: str, scene: Scene, expected_revision: int
    ) -> Registry:
        async with self._change(owner_id, expected_revision, "scene.created", scene.scene_id) as c:
            c.propose(scenes=(*c.current.scenes, scene))
            c.session.add(
                SmartHomeSceneRow(
                    owner_id=owner_id,
                    scene_id=scene.scene_id,
                    name=scene.name,
                    position=await c.next_position(SmartHomeSceneRow),
                )
            )
            await c.session.flush()
            c.session.add_all(_action_rows(owner_id, scene))
        return c.registry

    async def update_scene(
        self, *, owner_id: str, scene: Scene, expected_revision: int
    ) -> Registry:
        async with self._change(owner_id, expected_revision, "scene.updated", scene.scene_id) as c:
            row = await c.session.get(SmartHomeSceneRow, (owner_id, scene.scene_id))
            if row is None:
                raise SmartHomeRegistryNotFound("scene not found for owner")
            c.propose(scenes=_replace(c.current.scenes, "scene_id", scene))
            row.name = scene.name
            row.updated_at = utc_now()
            # The actions are the scene's content, so a new list replaces the
            # old one whole rather than being merged into it.
            await _delete_actions(c.session, owner_id, scene.scene_id)
            c.session.add_all(_action_rows(owner_id, scene))
        return c.registry

    async def delete_scene(
        self, *, owner_id: str, scene_id: str, expected_revision: int
    ) -> Registry:
        async with self._change(owner_id, expected_revision, "scene.deleted", scene_id) as c:
            row = await c.session.get(SmartHomeSceneRow, (owner_id, scene_id))
            if row is None:
                raise SmartHomeRegistryNotFound("scene not found for owner")
            c.propose(scenes=_without(c.current.scenes, "scene_id", scene_id))
            await _delete_actions(c.session, owner_id, scene_id)
            await c.session.delete(row)
        return c.registry

    # --- Placements -------------------------------------------------------

    async def set_placement(
        self, *, owner_id: str, placement: Placement, expected_revision: int
    ) -> Registry:
        """Say which area an Eidolon device stands in, replacing any earlier answer."""

        async with self._change(
            owner_id, expected_revision, "placement.set", placement.device_ref
        ) as c:
            row = await c.session.get(SmartHomePlacementRow, (owner_id, placement.device_ref))
            c.propose(
                placements=(
                    *_without(c.current.placements, "device_ref", placement.device_ref),
                    placement,
                )
            )
            if row is None:
                c.session.add(
                    SmartHomePlacementRow(
                        owner_id=owner_id,
                        device_ref=placement.device_ref,
                        area_id=placement.area_id,
                    )
                )
            else:
                row.area_id = placement.area_id
                row.updated_at = utc_now()
        return c.registry

    async def clear_placement(
        self, *, owner_id: str, device_ref: str, expected_revision: int
    ) -> Registry:
        async with self._change(owner_id, expected_revision, "placement.cleared", device_ref) as c:
            row = await c.session.get(SmartHomePlacementRow, (owner_id, device_ref))
            if row is None:
                raise SmartHomeRegistryNotFound("placement not found for owner")
            c.propose(placements=_without(c.current.placements, "device_ref", device_ref))
            await c.session.delete(row)
        return c.registry

    # --- Samples ----------------------------------------------------------

    async def load_sample(self, *, owner_id: str, name: str, expected_revision: int) -> Registry:
        """Fill an empty registry with one of the SDK's sample homes.

        Only an empty one: merging a sample into a home somebody has started
        would mean choosing whose "客厅" wins, and replacing it would delete
        their work. Both are refused rather than guessed.
        """

        factory = SAMPLES.get(name)
        if factory is None:
            raise SmartHomeRegistryNotFound(f"unknown smart home sample {name!r}")
        sample = factory()
        async with self._change(owner_id, expected_revision, "sample.loaded", name) as c:
            current = c.current
            if current.areas or current.devices or current.scenes or current.placements:
                raise SmartHomeRegistryRefused(
                    "a sample can only be loaded into an empty smart home registry",
                    code=REGISTRY_NOT_EMPTY,
                )
            c.propose(
                areas=sample.areas,
                devices=sample.devices,
                scenes=sample.scenes,
                placements=sample.placements,
            )
            c.session.add_all(
                SmartHomeAreaRow(
                    owner_id=owner_id,
                    area_id=area.area_id,
                    name=area.name,
                    sort_order=area.order,
                    position=index,
                )
                for index, area in enumerate(sample.areas)
            )
            await c.session.flush()
            c.session.add_all(
                SmartHomeDeviceRow(
                    owner_id=owner_id,
                    device_id=device.device_id,
                    position=index,
                    **_device_columns(device),
                )
                for index, device in enumerate(sample.devices)
            )
            c.session.add_all(
                SmartHomeSceneRow(
                    owner_id=owner_id, scene_id=scene.scene_id, name=scene.name, position=index
                )
                for index, scene in enumerate(sample.scenes)
            )
            await c.session.flush()
            for scene in sample.scenes:
                c.session.add_all(_action_rows(owner_id, scene))
            c.session.add_all(
                SmartHomePlacementRow(
                    owner_id=owner_id, device_ref=placement.device_ref, area_id=placement.area_id
                )
                for placement in sample.placements
            )
        return c.registry

    # --- Transaction ------------------------------------------------------

    @asynccontextmanager
    async def _change(
        self, owner_id: str, expected_revision: int, change: str, target: str
    ) -> AsyncIterator[_Change]:
        """One registry write: revision compare-and-swap, the change, one fact.

        The revision moves first. That makes the transaction a writer before it
        reads the registry, so what it then loads cannot change underneath it;
        a command that goes on to refuse rolls the move back with it.
        """

        if isinstance(expected_revision, bool) or expected_revision < 0:
            raise ValueError("expected_revision must be a non-negative integer")
        async with self._session_factory() as session, session.begin():
            if await session.get(OwnerRow, owner_id) is None:
                raise SmartHomeRegistryNotFound("owner not found")
            revision = await _advance_revision(session, owner_id, expected_revision)
            current = await load_registry(session, owner_id, revision=expected_revision)
            pending = _Change(session, owner_id, current, revision)
            yield pending
            session.add(
                governance_fact(
                    owner_id=owner_id,
                    subject_type="smarthome_registry",
                    subject_id=owner_id,
                    action="smarthome.registry.changed",
                    payload={
                        "revision": pending.registry.revision,
                        "change": change,
                        "target": target,
                    },
                )
            )


class _Change:
    """What one command sees and proposes inside its transaction."""

    def __init__(
        self, session: AsyncSession, owner_id: str, current: Registry, revision: int
    ) -> None:
        self.session = session
        self.owner_id = owner_id
        self.current = current
        self.revision = revision
        self.proposed: Registry | None = None

    @property
    def registry(self) -> Registry:
        """The registry this change produced; a command that proposed none is a bug."""

        if self.proposed is None:
            raise RuntimeError("smart home change proposed no registry")
        return self.proposed

    def propose(self, **parts: Any) -> Registry:
        """Build the resulting registry; the SDK accepts it or the command fails."""

        document = {
            "areas": self.current.areas,
            "devices": self.current.devices,
            "scenes": self.current.scenes,
            "placements": self.current.placements,
            **parts,
        }
        try:
            self.proposed = Registry(revision=self.revision, **document)
        except ValidationError as exc:
            refusal = _sdk_refusal(exc)
            if refusal is None:
                # Commands take SDK models, so fields are already valid; any
                # other failure is a contract change this code has not met and
                # is surfaced as it is rather than dressed up as a refusal.
                raise
            code, message = refusal
            raise SmartHomeRegistryInvalid(message, code=code) from exc
        return self.proposed

    async def next_position(self, row_type: type[Base]) -> int:
        highest = await self.session.scalar(
            select(func.max(row_type.position)).where(row_type.owner_id == self.owner_id)
        )
        return 0 if highest is None else highest + 1


async def _advance_revision(session: AsyncSession, owner_id: str, expected_revision: int) -> int:
    if expected_revision == 0:
        existing = await session.get(SmartHomeRegistryRow, owner_id)
        if existing is not None:
            raise SmartHomeRegistryConflict(expected_revision=0, current_revision=existing.revision)
        session.add(SmartHomeRegistryRow(owner_id=owner_id, revision=1))
        try:
            await session.flush()
        except IntegrityError as exc:
            # Another writer created it between the read and the insert.
            raise SmartHomeRegistryConflict(expected_revision=0, current_revision=1) from exc
        return 1
    result = await session.execute(
        update(SmartHomeRegistryRow)
        .where(
            SmartHomeRegistryRow.owner_id == owner_id,
            SmartHomeRegistryRow.revision == expected_revision,
        )
        .values(revision=SmartHomeRegistryRow.revision + 1, updated_at=utc_now())
    )
    if result.rowcount != 1:
        current = await session.scalar(
            select(SmartHomeRegistryRow.revision).where(SmartHomeRegistryRow.owner_id == owner_id)
        )
        raise SmartHomeRegistryConflict(
            expected_revision=expected_revision, current_revision=current or 0
        )
    return expected_revision + 1


def _sdk_refusal(error: ValidationError) -> tuple[str, str] | None:
    """The SDK's code for refusing a registry, and its words, if it gave one."""

    first = error.errors()[0]
    cause = (first.get("ctx") or {}).get("error")
    if isinstance(cause, RegistryError | SmartHomeError):
        return cause.code, str(cause)
    if first["type"] == "too_long" and len(first["loc"]) == 1:
        # A MAX_* cap is a field constraint, so pydantic reports it; the SDK
        # names the code and leaves the mapping to its callers.
        return (
            REGISTRY_LIMIT_EXCEEDED,
            f"{REGISTRY_LIMIT_EXCEEDED}: {first['loc'][0]}: {first['msg']}",
        )
    return None


def _replace(items: tuple, key: str, item: Any) -> tuple:
    identity = getattr(item, key)
    return tuple(item if getattr(existing, key) == identity else existing for existing in items)


def _without(items: tuple, key: str, identity: str) -> tuple:
    return tuple(existing for existing in items if getattr(existing, key) != identity)


def _device_columns(device: Device) -> dict[str, Any]:
    return {
        "name": device.name,
        "aliases_json": list(device.aliases),
        "device_type": device.type,
        "area_id": device.area_id,
        "provider": device.provider,
        "provider_ref": device.provider_ref,
    }


def _action_rows(owner_id: str, scene: Scene) -> list[SmartHomeSceneActionRow]:
    return [
        SmartHomeSceneActionRow(
            owner_id=owner_id,
            scene_id=scene.scene_id,
            position=index,
            device_id=action.device_id,
            trait=action.trait,
            command=action.command,
            params_json=dict(action.params),
        )
        for index, action in enumerate(scene.actions)
    ]


async def _delete_actions(session: AsyncSession, owner_id: str, scene_id: str) -> None:
    await session.execute(
        delete(SmartHomeSceneActionRow).where(
            SmartHomeSceneActionRow.owner_id == owner_id,
            SmartHomeSceneActionRow.scene_id == scene_id,
        )
    )
