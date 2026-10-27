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
        assert (await c.delete(detail, params={"expected_revision": 2})).status_code == 409
        assert (await c.delete(detail, params={"expected_revision": 1})).status_code == 204
        assert (await c.get(detail)).status_code == 404
