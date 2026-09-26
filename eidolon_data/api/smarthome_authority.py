"""Owner smart-home registry routes for the Workspace authority.

A router rather than an app: the registry is Owner master data written on
behalf of the Owner by Admin, so it rides on the Workspace authority's process
and write credential instead of opening a third port with a third token.

Bodies and responses are the SDK's ``eidolon_sdk.biz.smarthome`` models, never
rows. Every write carries ``expected_revision`` — the registry revision the
caller last read, 0 for a registry never written — and answers with the whole
registry it produced, so the caller has the new revision without re-reading.
"""

from __future__ import annotations

from collections.abc import Awaitable

from eidolon_sdk.biz.smarthome import Area, Device, Placement, Registry, Scene
from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from eidolon_data.services.smarthome import (
    SmartHomeRegistryConflict,
    SmartHomeRegistryError,
    SmartHomeRegistryInvalid,
    SmartHomeRegistryNotFound,
    SmartHomeRegistryService,
)

from .service_auth import authorize_service

PREFIX = "/api/workspace-authority/v1/owners/{owner_id}/smarthome"


class RegistryWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=0)


class AreaWrite(RegistryWrite):
    area: Area


class DeviceWrite(RegistryWrite):
    device: Device


class SceneWrite(RegistryWrite):
    scene: Scene


class PlacementWrite(RegistryWrite):
    area_id: str = Field(min_length=1, max_length=128)


def create_smarthome_router(registry: SmartHomeRegistryService, *, service_token: str) -> APIRouter:
    router = APIRouter(prefix=PREFIX, tags=["smarthome"])

    @router.get("/registry", response_model=Registry)
    async def get_registry(
        owner_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Registry:
        """This Owner's registry; revision 0 and empty when nothing was ever written."""

        authorize_service(authorization, service_token)
        return await registry.get_registry(owner_id)

    @router.post("/areas", response_model=Registry)
    async def create_area(
        owner_id: str,
        payload: AreaWrite,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Registry:
        authorize_service(authorization, service_token)
        return await _answer(
            registry.create_area(
                owner_id=owner_id, area=payload.area, expected_revision=payload.expected_revision
            )
        )

    @router.put("/areas/{area_id}", response_model=Registry)
    async def update_area(
        owner_id: str,
        area_id: str,
        payload: AreaWrite,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Registry:
        authorize_service(authorization, service_token)
        _same_identity(area_id, payload.area.area_id)
        return await _answer(
            registry.update_area(
                owner_id=owner_id, area=payload.area, expected_revision=payload.expected_revision
            )
        )

    @router.delete("/areas/{area_id}", response_model=Registry)
    async def delete_area(
        owner_id: str,
        area_id: str,
        expected_revision: int = Query(ge=0),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Registry:
        authorize_service(authorization, service_token)
        return await _answer(
            registry.delete_area(
                owner_id=owner_id, area_id=area_id, expected_revision=expected_revision
            )
        )

    @router.post("/devices", response_model=Registry)
    async def create_device(
        owner_id: str,
        payload: DeviceWrite,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Registry:
        authorize_service(authorization, service_token)
        return await _answer(
            registry.create_device(
                owner_id=owner_id,
                device=payload.device,
                expected_revision=payload.expected_revision,
            )
        )

    @router.put("/devices/{device_id}", response_model=Registry)
    async def update_device(
        owner_id: str,
        device_id: str,
        payload: DeviceWrite,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Registry:
        authorize_service(authorization, service_token)
        _same_identity(device_id, payload.device.device_id)
        return await _answer(
            registry.update_device(
                owner_id=owner_id,
                device=payload.device,
                expected_revision=payload.expected_revision,
            )
        )

    @router.delete("/devices/{device_id}", response_model=Registry)
    async def delete_device(
        owner_id: str,
        device_id: str,
        expected_revision: int = Query(ge=0),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Registry:
        authorize_service(authorization, service_token)
        return await _answer(
            registry.delete_device(
                owner_id=owner_id, device_id=device_id, expected_revision=expected_revision
            )
        )

    @router.post("/scenes", response_model=Registry)
    async def create_scene(
        owner_id: str,
        payload: SceneWrite,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Registry:
        authorize_service(authorization, service_token)
        return await _answer(
            registry.create_scene(
                owner_id=owner_id, scene=payload.scene, expected_revision=payload.expected_revision
            )
        )

    @router.put("/scenes/{scene_id}", response_model=Registry)
    async def update_scene(
        owner_id: str,
        scene_id: str,
        payload: SceneWrite,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Registry:
        authorize_service(authorization, service_token)
        _same_identity(scene_id, payload.scene.scene_id)
        return await _answer(
            registry.update_scene(
                owner_id=owner_id, scene=payload.scene, expected_revision=payload.expected_revision
            )
        )

    @router.delete("/scenes/{scene_id}", response_model=Registry)
    async def delete_scene(
        owner_id: str,
        scene_id: str,
        expected_revision: int = Query(ge=0),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Registry:
        authorize_service(authorization, service_token)
        return await _answer(
            registry.delete_scene(
                owner_id=owner_id, scene_id=scene_id, expected_revision=expected_revision
            )
        )

    @router.put("/placements/{device_ref}", response_model=Registry)
    async def set_placement(
        owner_id: str,
        device_ref: str,
        payload: PlacementWrite,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Registry:
        """Say which area an Eidolon device stands in; PUT because it states an end."""

        authorize_service(authorization, service_token)
        try:
            placement = Placement(device_ref=device_ref, area_id=payload.area_id)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors(include_context=False)) from exc
        return await _answer(
            registry.set_placement(
                owner_id=owner_id, placement=placement, expected_revision=payload.expected_revision
            )
        )

    @router.delete("/placements/{device_ref}", response_model=Registry)
    async def clear_placement(
        owner_id: str,
        device_ref: str,
        expected_revision: int = Query(ge=0),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Registry:
        authorize_service(authorization, service_token)
        return await _answer(
            registry.clear_placement(
                owner_id=owner_id, device_ref=device_ref, expected_revision=expected_revision
            )
        )

    @router.post("/samples/{name}", response_model=Registry)
    async def load_sample(
        owner_id: str,
        name: str,
        payload: RegistryWrite,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Registry:
        """Fill an empty registry with an SDK sample home; a started home is a 409."""

        authorize_service(authorization, service_token)
        return await _answer(
            registry.load_sample(
                owner_id=owner_id, name=name, expected_revision=payload.expected_revision
            )
        )

    return router


async def _answer(command: Awaitable[Registry]) -> Registry:
    try:
        return await command
    except SmartHomeRegistryError as exc:
        detail: dict[str, object] = {"code": exc.code, "message": str(exc)}
        if isinstance(exc, SmartHomeRegistryConflict):
            detail["current_revision"] = exc.current_revision
        raise HTTPException(status_code=_status(exc), detail=detail) from exc


def _status(error: SmartHomeRegistryError) -> int:
    if isinstance(error, SmartHomeRegistryNotFound):
        return 404
    if isinstance(error, SmartHomeRegistryInvalid):
        return 422
    # A stale revision and a registry whose state refuses the command are both
    # about state the caller can re-read; the code says which.
    return 409


def _same_identity(path_id: str, body_id: str) -> None:
    if path_id != body_id:
        raise HTTPException(status_code=422, detail="path and body identify different entities")
