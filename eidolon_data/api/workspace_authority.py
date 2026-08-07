"""Versioned command authority for first Owner workspace initialization."""

from __future__ import annotations

import hashlib
import json
from contextlib import asynccontextmanager
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from eidolon_data import DataSettings, DataStore, load_settings
from eidolon_data.services.owner_workspace import (
    OwnerWorkspaceError,
    OwnerWorkspaceInitializationResult,
)

from .service_auth import authorize_service, required_service_token


class WorkspaceInitializeRequest(BaseModel):
    """Minimal first-use input; later persona editing is a separate product flow."""

    model_config = ConfigDict(extra="forbid")

    owner_display_name: str = Field(min_length=1, max_length=128)
    companion_display_name: str = Field(default="Eidolon", min_length=1, max_length=128)


class OwnerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_id: str
    display_name: str
    lifecycle_state: Literal["active"] = "active"


class WorkspaceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["ready"] = "ready"
    primary_companion_id: str
    persona_genome_id: str
    memory_realm_id: str


class WorkspaceOperationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["owner-workspace.initialize"] = "owner-workspace.initialize"
    operation_id: str
    request_fingerprint: str
    status: Literal["succeeded"] = "succeeded"
    owner: OwnerResult
    workspace: WorkspaceResult


def create_app(
    settings: DataSettings | None = None,
    *,
    service_token: str | None = None,
) -> FastAPI:
    """Create the write authority without mounting legacy CRUD routes."""

    token = required_service_token(
        service_token,
        environment_name="EIDOLON_DATA_WORKSPACE_AUTHORITY_TOKEN",
    )
    store = DataStore.open(settings or load_settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await store.validate_schema()
        try:
            yield
        finally:
            await store.close()

    app = FastAPI(
        title="Eidolon Workspace Authority",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.get("/health", tags=["operations"])
    async def health() -> dict[str, str]:
        return {"status": "ready"}

    @app.put(
        "/api/workspace-authority/v1/operations/{operation_id}",
        response_model=WorkspaceOperationResponse,
        tags=["workspace-authority"],
    )
    async def initialize_workspace(
        operation_id: UUID,
        payload: WorkspaceInitializeRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> WorkspaceOperationResponse:
        authorize_service(authorization, token)
        fingerprint = _request_fingerprint(payload)
        try:
            result = await store.companion_workspaces.initialize_owner_workspace(
                operation_id=str(operation_id),
                request_fingerprint=fingerprint,
                owner_display_name=payload.owner_display_name,
                companion_display_name=payload.companion_display_name,
            )
        except OwnerWorkspaceError as exc:
            raise HTTPException(status_code=_workspace_error_status(exc), detail=str(exc)) from exc
        return _response(result)

    @app.get(
        "/api/workspace-authority/v1/operations/{operation_id}",
        response_model=WorkspaceOperationResponse,
        tags=["workspace-authority"],
    )
    async def get_workspace_operation(
        operation_id: UUID,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> WorkspaceOperationResponse:
        authorize_service(authorization, token)
        try:
            result = await store.companion_workspaces.get_owner_workspace_initialization(
                str(operation_id)
            )
        except OwnerWorkspaceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="workspace operation not found")
        return _response(result)

    return app


def _request_fingerprint(payload: WorkspaceInitializeRequest) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _response(result: OwnerWorkspaceInitializationResult) -> WorkspaceOperationResponse:
    workspace = result.workspace
    return WorkspaceOperationResponse(
        operation_id=result.operation_id,
        request_fingerprint=result.request_fingerprint,
        owner=OwnerResult(
            owner_id=result.owner.owner_id,
            display_name=result.owner.display_name,
        ),
        workspace=WorkspaceResult(
            primary_companion_id=workspace.companion.companion_id,
            persona_genome_id=workspace.persona_genome.genome_id,
            memory_realm_id=workspace.memory_realm.realm_id,
        ),
    )


def _workspace_error_status(error: OwnerWorkspaceError) -> int:
    message = str(error)
    if "already in use" in message or "belong to another" in message:
        return 409
    if "incomplete" in message or "inconsistent" in message:
        return 409
    return 400
