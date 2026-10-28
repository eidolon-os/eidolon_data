import pytest
from eidolon_sdk.biz.person import PersonCreate, PersonUpdate

from eidolon_data.schema.core import MemoryRealmRow
from eidolon_data.services.persons import PersonConflict


async def test_private_realms_default_on_idempotent_and_owner_scoped(store):
    await store.owner_commands.create_owner(owner_id="one")
    await store.owner_commands.create_owner(owner_id="two")
    for letter in ("a", "b"):
        await store.persons.create("one", PersonCreate(person_id=letter * 32, display_name=letter))
    assert (await store.persons.memory_realm("one", "a" * 32))["status"] == "active"
    a = await store.persons.memory_realm("one", "a" * 32, create=True)
    b = await store.persons.memory_realm("one", "b" * 32, create=True)
    assert a["realm_id"] != b["realm_id"]
    assert a == await store.persons.memory_realm("one", "a" * 32, create=True)
    with pytest.raises(LookupError):
        await store.persons.memory_realm("two", "a" * 32, create=True)
    await store.persons.delete("one", "a" * 32, 1)
    assert b == await store.persons.memory_realm("one", "b" * 32)
    with pytest.raises(PersonConflict):
        await store.persons.create(
            "one", PersonCreate(person_id="a" * 32, display_name="replacement")
        )
    async with store.persons._sessions() as session:
        retained = await session.get(MemoryRealmRow, a["realm_id"])
        assert retained.person_id == "a" * 32
        assert retained.status == "inactive"


async def test_inactive_person_does_not_reactivate_memory_implicitly(store):
    await store.owner_commands.create_owner(owner_id="one")
    await store.persons.create("one", PersonCreate(person_id="a" * 32, display_name="a"))
    await store.persons.memory_realm("one", "a" * 32, create=True)
    await store.persons.update(
        "one",
        "a" * 32,
        PersonUpdate(profile={}, expected_revision=1, display_name="a", status="inactive"),
    )
    with pytest.raises(PersonConflict):
        await store.persons.memory_realm("one", "a" * 32)
    await store.persons.update(
        "one",
        "a" * 32,
        PersonUpdate(profile={}, expected_revision=2, display_name="a", status="active"),
    )
    with pytest.raises(PersonConflict):
        await store.persons.memory_realm("one", "a" * 32, create=True)


async def test_restore_requires_active_person_current_revision_and_existing_realm(store):
    await store.owner_commands.create_owner(owner_id="one")
    await store.owner_commands.create_owner(owner_id="two")
    person_id = "a" * 32
    await store.persons.create("one", PersonCreate(person_id=person_id, display_name="a"))
    assert (await store.persons.restore_memory("one", person_id, 1))["person_revision"] == 1
    bound = await store.persons.memory_realm("one", person_id, create=True)
    await store.persons.update("one", person_id, PersonUpdate(
        profile={}, expected_revision=1, display_name="a", status="inactive"))
    with pytest.raises(PersonConflict):
        await store.persons.restore_memory("one", person_id, 2)
    await store.persons.update("one", person_id, PersonUpdate(
        profile={}, expected_revision=2, display_name="a", status="active"))
    with pytest.raises(PersonConflict):
        await store.persons.restore_memory("one", person_id, 2)
    with pytest.raises(LookupError):
        await store.persons.restore_memory("two", person_id, 3)
    with pytest.raises(PersonConflict):
        await store.persons.memory_realm("one", person_id)
    assert await store.persons.restore_memory("one", person_id, 3) == {**bound, "person_revision": 4}
    assert (await store.persons.get("one", person_id)).revision == 4
    assert await store.persons.restore_memory("one", person_id, 4) == {**bound, "person_revision": 4}
    assert (await store.persons.get("one", person_id)).revision == 4
    assert await store.persons.memory_realm("one", person_id) == {**bound, "person_revision": 4}
    await store.persons.delete("one", person_id, 4)
    with pytest.raises(LookupError):
        await store.persons.restore_memory("one", person_id, 4)
    async with store.persons._sessions() as session:
        assert (await session.get(MemoryRealmRow, bound["realm_id"])).status == "inactive"


async def test_memory_can_be_suspended_without_disabling_person_and_restored_in_place(store):
    await store.owner_commands.create_owner(owner_id="one")
    pid = "c" * 32
    await store.persons.create("one", PersonCreate(person_id=pid, display_name="c"))
    assert (await store.persons.memory_state("one", pid))["state"] == "active"
    bound = await store.persons.memory_realm("one", pid, create=True)
    suspended = await store.persons.suspend_memory("one", pid, 1)
    assert suspended["state"] == "inactive"
    assert suspended["person_status"] == "active"
    assert suspended["person_revision"] == 2
    assert await store.persons.suspend_memory("one", pid, 2) == suspended
    with pytest.raises(PersonConflict):
        await store.persons.memory_realm("one", pid)
    with pytest.raises(PersonConflict):
        await store.persons.restore_memory("one", pid, 1)
    restored = await store.persons.restore_memory("one", pid, 2)
    assert restored["realm_id"] == bound["realm_id"]
    assert restored["person_revision"] == 3
    assert (await store.persons.memory_state("one", pid))["state"] == "active"


async def test_retried_person_creation_does_not_restore_paused_memory(store):
    await store.owner_commands.create_owner(owner_id="one")
    request = PersonCreate(person_id="d" * 32, display_name="d")
    await store.persons.create("one", request)
    original = await store.persons.memory_realm("one", request.person_id)
    await store.persons.suspend_memory("one", request.person_id, 1)
    await store.persons.create("one", request)
    state = await store.persons.memory_state("one", request.person_id)
    assert state["state"] == "inactive"
    assert state["realm_id"] == original["realm_id"]
    assert state["person_revision"] == 2
