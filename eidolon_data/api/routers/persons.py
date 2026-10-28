"""Service-authenticated OS Person authority within the existing Data API."""

from eidolon_sdk.biz.person import PersonCreate, PersonUpdate, PersonView
from eidolon_sdk.biz.system_data.models import PersonalMemoryRealmSnapshot, PersonalMemoryState
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

    @router.get("/{person_id}/memory-realm", response_model=PersonalMemoryRealmSnapshot)
    async def get_memory_realm(owner_id: str, person_id: str):
        return await call(store.persons.memory_realm, owner_id, person_id)

    @router.put("/{person_id}/memory-realm", response_model=PersonalMemoryRealmSnapshot)
    async def ensure_memory_realm(owner_id: str, person_id: str):
        async def ensure(owner, person):
            return await store.persons.memory_realm(owner, person, create=True)
        return await call(ensure, owner_id, person_id)

    @router.post("/{person_id}/memory-realm/restore", response_model=PersonalMemoryRealmSnapshot)
    async def restore_memory_realm(
        owner_id: str, person_id: str, expected_revision: int = Query(ge=1)
    ):
        return await call(store.persons.restore_memory, owner_id, person_id, expected_revision)

    @router.get("/{person_id}/memory-state", response_model=PersonalMemoryState)
    async def memory_state(owner_id: str, person_id: str):
        return await call(store.persons.memory_state, owner_id, person_id)

    @router.post("/{person_id}/memory-realm/suspend", response_model=PersonalMemoryState)
    async def suspend_memory(owner_id: str, person_id: str, expected_revision: int = Query(ge=1)):
        return await call(store.persons.suspend_memory, owner_id, person_id, expected_revision)

    return router
