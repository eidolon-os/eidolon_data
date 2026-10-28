import httpx
from fastapi import FastAPI

from eidolon_data.api.routers.persons import create_person_router


async def test_person_authority_auth_scope_and_conflicts(store):
    await store.owner_commands.create_owner(owner_id="one")
    app = FastAPI()
    app.include_router(create_person_router(store, "secret"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        path = "/api/companion-authority/v1/owners/one/persons"
        assert (await c.get(path)).status_code in (401, 403)
        c.headers["Authorization"] = "Bearer secret"
        body = {"person_id": "a" * 32, "display_name": "甲"}
        assert (await c.post(path, json=body)).status_code == 200
        assert len((await c.get(path)).json()) == 1
        detail = path + "/" + body["person_id"]
        assert (
            await c.post(detail + "/archive", params={"expected_revision": 2})
        ).status_code == 409
        assert (
            await c.post(detail + "/archive", params={"expected_revision": 1})
        ).status_code == 204
        assert (await c.get(detail)).status_code == 404


async def test_person_memory_catalog_defaults_on_but_requires_service_auth(store):
    await store.owner_commands.create_owner(owner_id="one")
    await store.owner_commands.create_owner(owner_id="two")
    app = FastAPI()
    app.include_router(create_person_router(store, "secret"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        people = "/api/companion-authority/v1/owners/one/persons"
        path = people + "/" + "a" * 32 + "/memory-realm"
        assert (await c.put(path)).status_code in (401, 403)
        c.headers["Authorization"] = "Bearer secret"
        await c.post(people, json={"person_id": "a" * 32, "display_name": "a"})
        assert (await c.get(path)).status_code == 200
        first = await c.put(path)
        assert first.status_code == 200
        assert (await c.put(path)).json() == first.json()
        assert (await c.get(path)).json() == first.json()
        assert (await c.get(path.replace("/one/", "/two/"))).status_code == 404


async def test_companion_default_cannot_expose_personal_realm(store):
    from types import SimpleNamespace

    import pytest
    from eidolon_sdk.biz.person import PersonCreate
    from fastapi import HTTPException

    from eidolon_data.api.companion_authority import _runtime_snapshot

    await store.owner_commands.create_owner(owner_id="one")
    await store.persons.create("one", PersonCreate(person_id="a" * 32, display_name="a"))
    private = await store.persons.memory_realm("one", "a" * 32, create=True)
    companion = SimpleNamespace(
        owner_id="one", lifecycle_state="active", default_memory_realm_id=private["realm_id"]
    )
    with pytest.raises(HTTPException) as rejected:
        await _runtime_snapshot(store, companion, genome_id=None)
    assert rejected.value.status_code == 412


async def test_restore_memory_is_explicit_authenticated_and_revision_guarded(store):
    from eidolon_sdk.biz.person import PersonCreate, PersonUpdate

    await store.owner_commands.create_owner(owner_id="one")
    person_id = "a" * 32
    await store.persons.create("one", PersonCreate(person_id=person_id, display_name="a"))
    original = await store.persons.memory_realm("one", person_id, create=True)
    for revision, status in [(1, "inactive"), (2, "active")]:
        await store.persons.update(
            "one",
            person_id,
            PersonUpdate(profile={}, expected_revision=revision, display_name="a", status=status),
        )
    app = FastAPI()
    app.include_router(create_person_router(store, "secret"))
    path = f"/api/companion-authority/v1/owners/one/persons/{person_id}/memory-realm"
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as c:
        assert (await c.post(path + "/restore", params={"expected_revision": 3})).status_code in (
            401,
            403,
        )
        c.headers["Authorization"] = "Bearer secret"
        assert (await c.get(path)).status_code == 409
        assert (await c.put(path)).status_code == 409
        assert (await c.post(path + "/restore")).status_code == 422
        assert (await c.post(path + "/restore", params={"expected_revision": 2})).status_code == 409
        restored = await c.post(path + "/restore", params={"expected_revision": 3})
        assert restored.status_code == 200
        assert restored.json() == {**original, "person_revision": 4}
        assert (await c.get(path)).json() == {**original, "person_revision": 4}
