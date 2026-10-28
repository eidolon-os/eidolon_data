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
