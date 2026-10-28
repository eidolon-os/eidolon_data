import pytest
from eidolon_sdk.biz.person import PersonCreate, PersonUpdate

from eidolon_data.services.persons import PersonConflict

PID = "d" * 32


async def test_archive_restore_preserves_realm_but_never_old_authorization(store):
    await store.owner_commands.create_owner(owner_id="one")
    original = await store.persons.create("one", PersonCreate(person_id=PID, display_name="A"))
    realm = await store.persons.memory_realm("one", PID)
    await store.persons.delete("one", PID, original.revision)
    archived = (await store.persons.list("one", deleted=True))[0]
    assert archived.revision > original.revision
    assert await store.persons.list("one") == []
    with pytest.raises(LookupError):
        await store.persons.memory_realm("one", PID)
    with pytest.raises(PersonConflict):
        await store.persons.create("one", PersonCreate(person_id=PID, display_name="A"))
    restored = await store.persons.restore("one", PID, archived.revision)
    assert restored.status == "inactive" and restored.revision > archived.revision
    state = await store.persons.memory_state("one", PID)
    assert state["realm_id"] == realm["realm_id"] and state["state"] == "inactive"
    with pytest.raises(PersonConflict):
        await store.persons.restore_memory("one", PID, restored.revision)
    active = await store.persons.update(
        "one",
        PID,
        PersonUpdate(
            expected_revision=restored.revision,
            display_name="A",
            status="active",
            profile=restored.profile,
        ),
    )
    with pytest.raises(PersonConflict):
        await store.persons.memory_realm("one", PID)
    await store.persons.restore_memory("one", PID, active.revision)
    assert (await store.persons.memory_realm("one", PID))["realm_id"] == realm["realm_id"]


async def test_purge_is_owner_scoped_retryable_and_blocks_restore(store):
    for owner in ("one", "other"):
        await store.owner_commands.create_owner(owner_id=owner)
    await store.persons.create("one", PersonCreate(person_id=PID, display_name="A"))
    with pytest.raises(PersonConflict):
        await store.persons.purge_memory("one", PID, 1)
    await store.persons.delete("one", PID, 1)
    archived = (await store.persons.list("one", deleted=True))[0]
    intent = await store.persons.purge_memory("one", PID, archived.revision)
    current = (await store.persons.list("one", deleted=True))[0]
    assert await store.persons.purge_memory("one", PID, current.revision) == intent
    with pytest.raises(PersonConflict):
        await store.persons.restore("one", PID, current.revision)
    with pytest.raises(LookupError):
        await store.persons.purge_memory("other", PID, current.revision)
    with pytest.raises(PersonConflict):
        await store.persons.purge_memory("one", PID, 999, complete=True)
    completed = await store.persons.purge_memory("one", PID, intent["revision"], complete=True)
    assert completed["state"] == "completed"
    assert await store.persons.purge_memory("one", PID, intent["revision"]) == completed
    restored = await store.persons.restore(
        "one", PID, (await store.persons.list("one", deleted=True))[0].revision
    )
    assert restored.status == "inactive"
