"""Service-authenticated OS Person authority within the existing Data API."""

from eidolon_sdk.biz.person import PersonCreate, PersonUpdate, PersonView
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from eidolon_data.api.service_auth import authorize_service
from eidolon_data.services.persons import PersonConflict


def create_person_router(store, token):
    async def auth(authorization: str | None = Header(default=None)):
        authorize_service(authorization, token)

    router = APIRouter(
        prefix="/api/companion-authority/v1/owners/{owner_id}/persons", dependencies=[Depends(auth)]
    )

    async def call(fn, *args):
        try:
            return await fn(*args)
        except LookupError as exc:
            raise HTTPException(404, str(exc)) from exc
        except PersonConflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("", response_model=PersonView)
    async def create(owner_id: str, payload: PersonCreate):
        return await call(store.persons.create, owner_id, payload)

    @router.get("", response_model=list[PersonView])
    async def list_people(owner_id: str):
        return await call(store.persons.list, owner_id)

    @router.get("/{person_id}", response_model=PersonView)
    async def get(owner_id: str, person_id: str):
        return await call(store.persons.get, owner_id, person_id)

    @router.put("/{person_id}", response_model=PersonView)
    async def update(owner_id: str, person_id: str, payload: PersonUpdate):
        return await call(store.persons.update, owner_id, person_id, payload)

    @router.delete("/{person_id}", status_code=204)
    async def delete(owner_id: str, person_id: str, expected_revision: int = Query(ge=1)):
        await call(store.persons.delete, owner_id, person_id, expected_revision)
        return Response(status_code=204)

    return router
