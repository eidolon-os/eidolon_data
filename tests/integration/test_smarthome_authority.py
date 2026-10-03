"""The smart-home registry router, mounted the way the Workspace authority would."""

from __future__ import annotations

import httpx
import pytest
from eidolon_sdk.biz.smarthome.samples import apartment
from fastapi import FastAPI

from eidolon_data.api.smarthome_authority import create_smarthome_router
from eidolon_data.services.smarthome import SmartHomeRegistryService

pytestmark = pytest.mark.integration

TOKEN = "smarthome-authority-token-00001"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
BASE = "/api/workspace-authority/v1/owners/owner-a/smarthome"
LIVING = {"area_id": "living", "name": "客厅", "order": 0}
LAMP = {"device_id": "living.lamp", "name": "落地灯", "type": "light", "area_id": "living"}


@pytest.fixture
async def client(store):
    await store.owner_commands.create_owner(owner_id="owner-a")
    app = FastAPI()
    app.include_router(
        create_smarthome_router(
            SmartHomeRegistryService(store.owners._session_factory), service_token=TOKEN
        )
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://data.test"
    ) as value:
        yield value


async def test_routes_require_the_service_credential(client) -> None:
    assert (await client.get(f"{BASE}/registry")).status_code == 401
    assert (
        await client.get(f"{BASE}/registry", headers={"Authorization": "Bearer wrong-token"})
    ).status_code == 403


async def test_writes_answer_with_the_registry_they_produced(client) -> None:
    empty = await client.get(f"{BASE}/registry", headers=HEADERS)
    assert empty.status_code == 200
    assert empty.json()["revision"] == 0

    created = await client.post(
        f"{BASE}/areas", json={"expected_revision": 0, "area": LIVING}, headers=HEADERS
    )
    assert created.status_code == 200
    assert created.json()["revision"] == 1
    device = await client.post(
        f"{BASE}/devices",
        json={"expected_revision": 1, "device": {**LAMP, "aliases": ["台灯"]}},
        headers=HEADERS,
    )
    assert device.json()["devices"][0]["aliases"] == ["台灯"]
    renamed = await client.put(
        f"{BASE}/areas/living",
        json={"expected_revision": 2, "area": {**LIVING, "name": "起居室"}},
        headers=HEADERS,
    )
    placed = await client.put(
        f"{BASE}/placements/korvo-1",
        json={"expected_revision": 3, "area_id": "living"},
        headers=HEADERS,
    )
    assert renamed.json()["areas"][0]["name"] == "起居室"
    assert placed.json()["placements"] == [{"device_ref": "korvo-1", "area_id": "living"}]
    assert (await client.get(f"{BASE}/registry", headers=HEADERS)).json() == placed.json()


async def test_refusals_carry_a_status_and_a_code(client) -> None:
    await client.post(
        f"{BASE}/areas", json={"expected_revision": 0, "area": LIVING}, headers=HEADERS
    )
    await client.post(
        f"{BASE}/devices", json={"expected_revision": 1, "device": LAMP}, headers=HEADERS
    )

    stale = await client.post(
        f"{BASE}/areas",
        json={"expected_revision": 1, "area": {"area_id": "master", "name": "主卧"}},
        headers=HEADERS,
    )
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "REVISION_CONFLICT"
    assert stale.json()["detail"]["current_revision"] == 2

    not_empty = await client.delete(
        f"{BASE}/areas/living", params={"expected_revision": 2}, headers=HEADERS
    )
    assert not_empty.status_code == 409
    assert not_empty.json()["detail"]["code"] == "AREA_NOT_EMPTY"

    invalid = await client.post(
        f"{BASE}/devices",
        json={"expected_revision": 2, "device": {**LAMP, "device_id": "x", "area_id": "attic"}},
        headers=HEADERS,
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "DEVICE_AREA_UNKNOWN"

    missing = await client.delete(
        f"{BASE}/scenes/ghost", params={"expected_revision": 2}, headers=HEADERS
    )
    assert missing.status_code == 404
    assert missing.json()["detail"]["code"] == "NOT_FOUND"

    mismatched = await client.put(
        f"{BASE}/areas/master", json={"expected_revision": 2, "area": LIVING}, headers=HEADERS
    )
    assert mismatched.status_code == 422
    assert (await client.get(f"{BASE}/registry", headers=HEADERS)).json()["revision"] == 2


async def test_a_duplicate_device_name_in_one_area_is_a_422(client) -> None:
    await client.post(
        f"{BASE}/areas", json={"expected_revision": 0, "area": LIVING}, headers=HEADERS
    )
    await client.post(
        f"{BASE}/areas",
        json={"expected_revision": 1, "area": {"area_id": "master", "name": "主卧"}},
        headers=HEADERS,
    )
    await client.post(
        f"{BASE}/devices", json={"expected_revision": 2, "device": LAMP}, headers=HEADERS
    )
    elsewhere = {**LAMP, "device_id": "master.lamp", "area_id": "master"}
    created = await client.post(
        f"{BASE}/devices", json={"expected_revision": 3, "device": elsewhere}, headers=HEADERS
    )
    assert created.status_code == 200

    duplicate = await client.post(
        f"{BASE}/devices",
        json={"expected_revision": 4, "device": {**LAMP, "device_id": "living.lamp2"}},
        headers=HEADERS,
    )
    moved = await client.put(
        f"{BASE}/devices/master.lamp",
        json={"expected_revision": 4, "device": {**elsewhere, "area_id": "living"}},
        headers=HEADERS,
    )
    for response in (duplicate, moved):
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "DUPLICATE_DEVICE_NAME_IN_AREA"
    assert (await client.get(f"{BASE}/registry", headers=HEADERS)).json()["revision"] == 4


async def test_the_sample_loads_once_into_an_empty_home(client) -> None:
    loaded = await client.post(
        f"{BASE}/samples/apartment", json={"expected_revision": 0}, headers=HEADERS
    )
    assert loaded.status_code == 200
    assert loaded.json() == apartment().model_dump(mode="json")

    again = await client.post(
        f"{BASE}/samples/apartment", json={"expected_revision": 1}, headers=HEADERS
    )
    assert again.status_code == 409
    assert again.json()["detail"]["code"] == "REGISTRY_NOT_EMPTY"
    unknown = await client.post(
        f"{BASE}/samples/castle", json={"expected_revision": 1}, headers=HEADERS
    )
    assert unknown.status_code == 404


async def test_device_scene_and_placement_routes_round_trip(client) -> None:
    await client.post(
        f"{BASE}/areas", json={"expected_revision": 0, "area": LIVING}, headers=HEADERS
    )
    await client.post(
        f"{BASE}/devices", json={"expected_revision": 1, "device": LAMP}, headers=HEADERS
    )
    scene = {
        "scene_id": "scene.movie",
        "name": "观影",
        "actions": [{"device_id": "living.lamp", "trait": "on_off", "command": "on"}],
    }
    steps = [
        ("put", f"{BASE}/devices/living.lamp", {"device": {**LAMP, "name": "台灯"}}),
        ("post", f"{BASE}/scenes", {"scene": scene}),
        (
            "put",
            f"{BASE}/scenes/scene.movie",
            {
                "scene": {
                    **scene,
                    "actions": [
                        {
                            "device_id": "living.lamp",
                            "trait": "level",
                            "command": "set",
                            "params": {"value": 15},
                        }
                    ],
                }
            },
        ),
        ("put", f"{BASE}/placements/korvo-1", {"area_id": "living"}),
    ]
    revision = 2
    for method, path, body in steps:
        response = await client.request(
            method, path, json={"expected_revision": revision, **body}, headers=HEADERS
        )
        assert response.status_code == 200, response.text
        revision += 1
        assert response.json()["revision"] == revision
    assert response.json()["devices"][0]["name"] == "台灯"
    assert response.json()["scenes"][0]["actions"][0]["params"] == {"value": 15}

    bad_ref = await client.put(
        f"{BASE}/placements/not a ref",
        json={"expected_revision": revision, "area_id": "living"},
        headers=HEADERS,
    )
    assert bad_ref.status_code == 422
    for path in (
        f"{BASE}/placements/korvo-1",
        f"{BASE}/scenes/scene.movie",
        f"{BASE}/devices/living.lamp",
        f"{BASE}/areas/living",
    ):
        response = await client.delete(
            path, params={"expected_revision": revision}, headers=HEADERS
        )
        assert response.status_code == 200, response.text
        revision += 1
    assert response.json() == {
        "schema_version": 1,
        "revision": revision,
        "areas": [],
        "devices": [],
        "scenes": [],
        "placements": [],
    }


async def test_v2_fields_round_trip_through_rows(client) -> None:
    await client.post(
        f"{BASE}/areas", json={"expected_revision": 0, "area": LIVING}, headers=HEADERS
    )
    imported = {
        **LAMP,
        "provider": "homeassistant:acc_1",
        "provider_ref": "light.living_lamp",
        "traits": ["on_off"],
        "limits": None,
        "source": "imported",
        "overrides": ["name"],
        "synced_at_ms": 1_700_000_000_000,
        "orphaned": False,
    }
    created = await client.post(
        f"{BASE}/devices", json={"expected_revision": 1, "device": imported}, headers=HEADERS
    )
    assert created.status_code == 200, created.text
    stored = created.json()["devices"][0]
    assert stored["traits"] == ["on_off"] and stored["source"] == "imported"
    assert stored["overrides"] == ["name"] and stored["synced_at_ms"] == 1_700_000_000_000
    ac = {
        "device_id": "living.ac",
        "name": "空调",
        "type": "climate",
        "area_id": "living",
        "provider": "homeassistant:acc_1",
        "provider_ref": "climate.ac",
        "limits": {"target_c": [18, 28], "modes": ["cool", "heat"]},
        "source": "imported",
    }
    created = await client.post(
        f"{BASE}/devices", json={"expected_revision": 2, "device": ac}, headers=HEADERS
    )
    assert created.status_code == 200, created.text
    assert created.json()["devices"][1]["limits"] == {
        "target_c": [18, 28],
        "modes": ["cool", "heat"],
    }
    scene = {
        "scene_id": "scene.home",
        "name": "回家",
        "provider_ref": "scene.home",
        "provider": "homeassistant:acc_1",
    }
    created = await client.post(
        f"{BASE}/scenes", json={"expected_revision": 3, "scene": scene}, headers=HEADERS
    )
    assert created.status_code == 200, created.text
    assert created.json()["scenes"][0] == {**scene, "actions": []}
    orphaned = await client.put(
        f"{BASE}/devices/living.lamp",
        json={"expected_revision": 4, "device": {**imported, "orphaned": True}},
        headers=HEADERS,
    )
    assert orphaned.status_code == 200 and orphaned.json()["devices"][0]["orphaned"] is True
    # Read back through the registry route: the same document.
    registry = await client.get(f"{BASE}/registry", headers=HEADERS)
    assert registry.json() == orphaned.json()
