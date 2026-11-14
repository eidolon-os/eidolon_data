import base64
import io
import httpx
from PIL import Image
from fastapi import FastAPI
from eidolon_sdk.biz.person import PersonCreate
from eidolon_data.api.routers.persons import create_person_router

async def test_face_api_requires_owner_auth_and_revision_and_disallows_recognition_fields(store):
    await store.owner_commands.create_owner(owner_id="one")
    await store.owner_commands.create_owner(owner_id="two")
    pid = "a" * 32
    await store.persons.create("one", PersonCreate(person_id=pid, display_name="A"))
    app = FastAPI(); app.include_router(create_person_router(store, "secret"))
    path = f"/api/companion-authority/v1/owners/one/persons/{pid}/face-profile"
    output = io.BytesIO(); Image.new("RGB", (8, 8), "red").save(output, format="PNG")
    body = dict(expected_revision=0, image_base64=base64.b64encode(output.getvalue()).decode())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        for method in ('get','put','delete'):
            assert (await c.request(method, path, json=body)).status_code in (401,403)
        c.headers['Authorization'] = 'Bearer secret'
        assert (await c.put(path, json={**body, 'model_id':'fake'})).status_code == 422
        assert (await c.put(path, json={**body, 'image_base64':'broken'})).status_code == 422
        assert (await c.put(path, json=body)).status_code == 200
        assert (await c.put(path, json=body)).status_code == 409
        image = await c.get(path+'/image')
        assert image.status_code == 200 and image.headers['cache-control'] == 'no-store'
        assert (await c.get(path.replace('/one/', '/two/')+'/image')).status_code == 404
        assert (await c.delete(path)).status_code == 422
        assert (await c.delete(path+'?expected_revision=1')).json()['has_image'] is False


async def test_recognition_api_stores_consented_features_and_serves_them_only_as_candidates(store):
    from eidolon_data.api.routers.persons import create_face_recognition_router

    await store.owner_commands.create_owner(owner_id="one")
    await store.owner_commands.create_owner(owner_id="two")
    pid = "a" * 32
    await store.persons.create("one", PersonCreate(person_id=pid, display_name="A"))
    app = FastAPI()
    app.include_router(create_person_router(store, "secret"))
    app.include_router(create_face_recognition_router(store, "secret"))
    owner = "/api/companion-authority/v1/owners/one"
    path = f"{owner}/persons/{pid}/face-profile"
    candidates = f"{owner}/faces/recognition-candidates"
    output = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(output, format="PNG")
    photo = dict(expected_revision=0, image_base64=base64.b64encode(output.getvalue()).decode())
    body = dict(expected_revision=1,
        consent=dict(consent_version="face-1", statement="They agreed.", attested=True),
        features=dict(model_id="face-model", weights_hash="c" * 64, preprocessing_version="prep-1",
                      vector=[0.25, 0.5, -0.125]))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        for method, url in (("put", path + "/recognition"), ("delete", path + "/recognition"),
                            ("get", candidates)):
            assert (await c.request(method, url, json=body)).status_code in (401, 403)
        c.headers["Authorization"] = "Bearer secret"
        assert (await c.put(path + "/recognition", json=body)).status_code == 409  # no photo
        assert (await c.put(path, json=photo)).status_code == 200
        assert (await c.put(path + "/recognition", json={**body, "expected_revision": 2})).status_code == 409
        unattested = {**body, "consent": {**body["consent"], "attested": False}}
        assert (await c.put(path + "/recognition", json=unattested)).status_code == 422
        assert (await c.put(path.replace("/one/", "/two/") + "/recognition", json=body)).status_code == 404
        enabled = await c.put(path + "/recognition", json=body)
        assert enabled.status_code == 200 and enabled.headers["cache-control"] == "no-store"
        assert enabled.json()["recognition_state"] == "enabled"
        state = await c.get(path)
        assert state.json() == enabled.json() and state.json()["recognition_consent_version"] == "face-1"
        # The feature leaves Data only through the candidates contract.
        for response in (enabled, state, await c.get(path + "/image")):
            assert "vector" not in response.text and "0.125" not in response.text
        listed = await c.get(candidates)
        assert listed.status_code == 200 and listed.headers["cache-control"] == "no-store"
        assert [p["person_id"] for p in listed.json()["persons"]] == [pid]
        assert listed.json()["persons"][0]["templates"][0]["features"]["vector"] == [0.25, 0.5, -0.125]
        assert (await c.get(candidates.replace("/one/", "/two/"))).json()["persons"] == []
        assert (await c.get(candidates.replace("/one/", "/none/"))).status_code == 404
        disabled = await c.delete(path + "/recognition")
        assert disabled.status_code == 200 and disabled.headers["cache-control"] == "no-store"
        assert disabled.json()["recognition_state"] == "not_configured" and disabled.json()["has_image"]
        assert (await c.delete(path + "/recognition")).json() == disabled.json()
        emptied = await c.get(candidates)
        assert emptied.json()["persons"] == [] and emptied.json()["revision"] != listed.json()["revision"]


def test_companion_authority_serves_face_recognition_candidates(tmp_path):
    from eidolon_data import DataSettings
    from eidolon_data.api.companion_authority import create_app

    app = create_app(DataSettings(sqlite_path=str(tmp_path / "authority.sqlite3")),
                     service_token="face-authority-token-0000000001",
                     memory_roster_token="face-roster-token-000000000001")
    assert "/api/companion-authority/v1/owners/{owner_id}/faces/recognition-candidates" in app.openapi()["paths"]
