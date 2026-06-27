"""Owner HTTP routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from eidolon_data.api.schemas import OwnerCreateRequest, OwnerResponse

router = APIRouter(prefix="/owners", tags=["owners"])


@router.post("", response_model=OwnerResponse)
async def create_owner(payload: OwnerCreateRequest, request: Request) -> OwnerResponse:
    store = request.app.state.store
    row = await store.owners.create(
        owner_id=payload.owner_id,
        display_name=payload.display_name,
        kind=payload.kind,
        profile_json=payload.profile_json,
        settings_json=payload.settings_json,
    )
    return _owner_response(row)


@router.get("", response_model=list[OwnerResponse])
async def list_owners(request: Request) -> list[OwnerResponse]:
    store = request.app.state.store
    return [_owner_response(row) for row in await store.owners.list()]


@router.get("/{owner_id}", response_model=OwnerResponse)
async def get_owner(owner_id: str, request: Request) -> OwnerResponse:
    store = request.app.state.store
    row = await store.owners.get(owner_id)
    if row is None:
        raise HTTPException(status_code=404, detail="owner not found")
    return _owner_response(row)


def _owner_response(row) -> OwnerResponse:
    return OwnerResponse(
        owner_id=row.owner_id,
        display_name=row.display_name,
        kind=row.kind,
        profile_json=row.profile_json,
        settings_json=row.settings_json,
    )

