import pytest
from eidolon_sdk.biz.person import PersonCreate, PersonUpdate
from eidolon_sdk.biz.system_data.models import ProvisionalMemoryPolicy

from eidolon_data.schema.core import MemoryRealmRow
from eidolon_data.schema.persons import PersonRow
from eidolon_data.services.persons import PersonConflict

PERSON = "a" * 32


async def setup_candidate(store):
    await store.owner_commands.create_owner(owner_id="owner")
    await store.persons.create("owner", PersonCreate(
        person_id=PERSON, display_name="anonymous", maturity="candidate", is_named=False))


@pytest.mark.parametrize("save,recall", [(False, False), (True, False), (False, True), (True, True)])
async def test_independent_controls_preserve_inactive_realm(store, save, recall):
    await setup_candidate(store)
    original = await store.persons.provisional_memory_policy("owner", PERSON)
    assert original["policy"] == {"save_allowed": False, "session_recall_allowed": False}
    policy = ProvisionalMemoryPolicy(save_allowed=save, session_recall_allowed=recall)
    changed = await store.persons.provisional_memory_policy("owner", PERSON, 1, policy)
    revision = changed["person_revision"]
    assert revision == (2 if save or recall else 1)
    assert changed == await store.persons.provisional_memory_policy("owner", PERSON, revision, policy)
    access = await store.persons.provisional_memory_policy("owner", PERSON, revision, None, "session")
    assert access["policy"] == policy.model_dump()
    assert access["session_id"] == "session"
    async with store.persons._sessions() as session:
        realm = await session.get(MemoryRealmRow, original["realm_id"])
        assert realm.status == "inactive"
        assert realm.policy_json["scope"] == "person"
    with pytest.raises(PersonConflict):
        await store.persons.memory_realm("owner", PERSON)
    with pytest.raises(LookupError):
        await store.persons.provisional_memory_policy("other", PERSON, revision, None, "session")
    with pytest.raises(PersonConflict):
        await store.persons.provisional_memory_policy("owner", PERSON, revision + 1, None, "session")


@pytest.mark.parametrize("action", ["suspend", "disable", "archive", "expire", "retire"])
async def test_provisional_access_stops_with_lifecycle(store, action):
    await setup_candidate(store)
    await store.persons.provisional_memory_policy("owner", PERSON, 1,
        ProvisionalMemoryPolicy(save_allowed=True, session_recall_allowed=True))
    if action == "suspend":
        result = await store.persons.suspend_memory("owner", PERSON, 2)
        assert result["person_revision"] == 3  # inactive Realm still has a revocable policy
    elif action == "disable":
        await store.persons.update("owner", PERSON, PersonUpdate(
            expected_revision=2, display_name="anonymous", status="inactive", profile={}))
        await store.persons.update("owner", PERSON, PersonUpdate(
            expected_revision=3, display_name="anonymous", status="active", profile={}))
    elif action == "archive":
        await store.persons.archive("owner", PERSON, 2)
        await store.persons.restore("owner", PERSON, 3)
    elif action == "expire":
        async with store.persons._transaction() as session:
            person = await session.get(PersonRow, PERSON)
            person.candidate_activity_at = 0
    else:
        await store.persons.retire_candidate("owner", PERSON, 2)
    state = await store.persons.provisional_memory_policy("owner", PERSON)
    access = await store.persons.provisional_memory_policy(
        "owner", PERSON, state["person_revision"], None, "session")
    assert not any(access["policy"].values())


async def test_provisional_policy_api_requires_service_auth_and_current_revision(store):
    import httpx
    from fastapi import FastAPI

    from eidolon_data.api.routers.persons import create_person_router

    await setup_candidate(store)
    app = FastAPI()
    app.include_router(create_person_router(store, "secret"))
    path = f"/api/companion-authority/v1/owners/owner/persons/{PERSON}/provisional-memory"
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get(path + "/policy")).status_code in (401, 403)
        client.headers["authorization"] = "Bearer secret"
        assert (await client.get(path + "/policy")).json()["person_revision"] == 1
        response = await client.put(path + "/policy?expected_revision=1", json={"save_allowed": True})
        assert response.status_code == 200
        assert response.json()["policy"] == {"save_allowed": True, "session_recall_allowed": False}
        assert (await client.get(path + "/access?expected_revision=1&session_id=s")).status_code == 409
        access = await client.get(path + "/access?expected_revision=2&session_id=s")
        assert access.status_code == 200 and access.json()["scope"] == "provisional_session"
        assert (await client.get(path + "/access?expected_revision=2&session_id=%20")).status_code == 422


async def test_policy_changes_require_revision_and_do_not_restore_inactive_person(store):
    await setup_candidate(store)
    policy = ProvisionalMemoryPolicy(save_allowed=True)
    with pytest.raises(PersonConflict):
        await store.persons.provisional_memory_policy("owner", PERSON, policy=policy)
    await store.persons.update("owner", PERSON, PersonUpdate(
        expected_revision=1, display_name="anonymous", status="inactive", profile={}))
    with pytest.raises(PersonConflict):
        await store.persons.provisional_memory_policy("owner", PERSON, 1, policy)
    with pytest.raises(PersonConflict):
        await store.persons.provisional_memory_policy("owner", PERSON, 2, policy)
    state = await store.persons.provisional_memory_policy("owner", PERSON)
    assert state["person_revision"] == 2 and not any(state["policy"].values())
