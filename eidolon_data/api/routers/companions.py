"""Companion HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from eidolon_data.api.schemas import CompanionCreateRequest, CompanionResponse

router = APIRouter(prefix="/companions", tags=["companions"])


@router.post("", response_model=CompanionResponse)
async def create_companion(
    payload: CompanionCreateRequest,
    request: Request,
) -> CompanionResponse:
    store = request.app.state.store
    row = await store.companions.create(
        companion_id=payload.companion_id,
        owner_id=payload.owner_id,
        display_name=payload.display_name,
        kind=payload.kind,
        status=payload.status,
        profile_json=payload.profile_json,
        runtime_config_json=payload.runtime_config_json,
        metadata_json=payload.metadata_json,
    )
    return _companion_response(row)


@router.get("/{companion_id}", response_model=CompanionResponse)
async def get_companion(companion_id: str, request: Request) -> CompanionResponse:
    store = request.app.state.store
    row = await store.companions.get(companion_id)
    if row is None:
        raise HTTPException(status_code=404, detail="companion not found")
    return _companion_response(row)


@router.get("/by-owner/{owner_id}", response_model=list[CompanionResponse])
async def list_companions_for_owner(owner_id: str, request: Request) -> list[CompanionResponse]:
    store = request.app.state.store
    rows = await store.companions.list_for_owner(owner_id)
    return [_companion_response(row) for row in rows]


def _companion_response(row) -> CompanionResponse:
    return CompanionResponse(
        companion_id=row.companion_id,
        owner_id=row.owner_id,
        display_name=row.display_name,
        kind=row.kind,
        status=row.status,
        current_genome_id=row.current_genome_id,
        default_memory_realm_id=row.default_memory_realm_id,
        profile_json=row.profile_json,
        runtime_config_json=row.runtime_config_json,
        metadata_json=row.metadata_json,
    )

