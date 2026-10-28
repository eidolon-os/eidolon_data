"""Service-authenticated OS Person authority within the existing Data API."""

from typing import Literal

from eidolon_sdk.biz.person import PersonCreate, PersonUpdate, PersonView
from eidolon_sdk.biz.person_face import PersonFaceImage, PersonFaceState, PersonFaceUpdate
from eidolon_sdk.biz.system_data.models import PersonalMemoryRealmSnapshot, PersonalMemoryState
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response

from eidolon_data.api.service_auth import authorize_service
from eidolon_data.services.person_faces import PersonFaceInvalid
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
        except PersonFaceInvalid as exc:
            raise HTTPException(422, str(exc)) from exc
        except PersonConflict as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("", response_model=PersonView)
    async def create(owner_id: str, payload: PersonCreate):
        return await call(store.persons.create, owner_id, payload)

    @router.get("", response_model=list[PersonView])
    async def list_people(owner_id: str):
        return await call(store.persons.list, owner_id)

    @router.get("/archived", response_model=list[PersonView])
    async def archived(owner_id: str):
        async def read(owner):
            return await store.persons.list(owner, deleted=True)

        return await call(read, owner_id)

    @router.post("/{person_id}/restore", response_model=PersonView)
    async def restore(owner_id: str, person_id: str, expected_revision: int = Query(ge=1)):
        return await call(store.persons.restore, owner_id, person_id, expected_revision)

    @router.post("/{person_id}/memory-purge/{phase}")
    async def purge_memory(
        owner_id: str,
        person_id: str,
        phase: Literal["begin", "complete"],
        expected_revision: int = Query(ge=1),
    ):
        async def invoke(owner, person, revision):
            return await store.persons.purge_memory(
                owner, person, revision, complete=phase == "complete"
            )

        return await call(invoke, owner_id, person_id, expected_revision)

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

    @router.get("/{person_id}/face-profile", response_model=PersonFaceState)
    async def face_state(owner_id: str, person_id: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return await call(store.person_faces.state, owner_id, person_id)

    @router.get("/{person_id}/face-profile/image", response_model=PersonFaceImage)
    async def face_image(owner_id: str, person_id: str, response: Response):
        response.headers["Cache-Control"] = "no-store"
        return await call(store.person_faces.image, owner_id, person_id)

    @router.put("/{person_id}/face-profile", response_model=PersonFaceState)
    async def face_replace(owner_id: str, person_id: str, payload: PersonFaceUpdate):
        return await call(store.person_faces.replace, owner_id, person_id, payload)

    @router.delete("/{person_id}/face-profile", response_model=PersonFaceState)
    async def face_delete(owner_id: str, person_id: str, expected_revision: int = Query(ge=0)):
        return await call(store.person_faces.delete, owner_id, person_id, expected_revision)

    return router
