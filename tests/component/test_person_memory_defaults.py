import httpx
import pytest
from eidolon_sdk.biz.person import PersonCreate
from eidolon_sdk.biz.system_data.models import PersonMemoryDefaults, ProvisionalMemoryPolicy
from fastapi import FastAPI

from eidolon_data.api.routers.persons import create_person_router
from eidolon_data.services.persons import PersonConflict


async def test_defaults_snapshot_only_new_candidates_and_preserve_other_owner_settings(store):
    await store.owner_commands.create_owner(owner_id="owner", settings_json={"other": "keep"})
    old = PersonCreate(person_id="a" * 32, display_name="old", maturity="candidate")
    await store.persons.create("owner", old)
    initial = await store.persons.memory_defaults("owner")
    policy = PersonMemoryDefaults(candidate=ProvisionalMemoryPolicy(save_allowed=True))
    changed = await store.persons.memory_defaults("owner", initial["owner_revision"], policy)
    assert changed["owner_revision"] == initial["owner_revision"] + 1
    assert await store.persons.memory_defaults("owner", changed["owner_revision"], policy) == changed
    with pytest.raises(PersonConflict):
        await store.persons.memory_defaults("owner", initial["owner_revision"], policy)
    with pytest.raises(LookupError):
        await store.persons.memory_defaults("missing")
    assert (await store.owners.get("owner")).settings_json["other"] == "keep"
    await store.persons.create("owner", old)  # Idempotent creation cannot refresh permissions.
    assert not (await store.persons.provisional_memory_policy("owner", old.person_id))["policy"]["save_allowed"]
    await store.persons.create("owner", old.model_copy(update={"person_id": "b" * 32}))
    new = await store.persons.provisional_memory_policy("owner", "b" * 32)
    assert new["policy"] == {"save_allowed": True, "session_recall_allowed": False}
    assert (await store.persons.memory_state("owner", "b" * 32))["state"] == "inactive"
    await store.persons.memory_defaults("owner", changed["owner_revision"], PersonMemoryDefaults())
    assert (await store.persons.provisional_memory_policy("owner", "b" * 32))["policy"] == new["policy"]


async def test_owner_defaults_api_auth_cas_and_strict_contract(store):
    await store.owner_commands.create_owner(owner_id="owner")
    app = FastAPI()
    app.include_router(create_person_router(store, "secret"))
    path = "/api/companion-authority/v1/owners/owner/persons/memory-policy"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(path)).status_code in (401, 403)
        client.headers["Authorization"] = "Bearer secret"
        initial = (await client.get(path)).json()
        revision = initial["owner_revision"]
        assert not initial["policy"]["prepare_on_promotion"]
        assert (await client.put(path, json={})).status_code == 422
        assert (await client.put(path, params={"expected_revision": revision},
                                 json={"prepare_on_promotion": "true"})).status_code == 422
        changed = await client.put(path, params={"expected_revision": revision},
                                   json={"prepare_on_promotion": True})
        assert changed.status_code == 200
        assert (await client.put(path, params={"expected_revision": revision}, json={})).status_code == 409
