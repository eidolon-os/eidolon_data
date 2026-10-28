import pytest
from eidolon_sdk.biz.person import PersonCreate, PersonProfile, PersonUpdate

from eidolon_data.services.persons import PersonConflict

PID = "a" * 32


async def test_lifecycle_scope_retry_and_revision(store):
    for owner in ("one", "two"):
        await store.owner_commands.create_owner(owner_id=owner)
    request = PersonCreate(person_id=PID, display_name="甲")
    person = await store.persons.create("one", request)
    assert person == await store.persons.create("one", request)
    assert len(await store.persons.list("one")) == 1
    assert await store.persons.list("two") == []
    with pytest.raises(LookupError):
        await store.persons.get("two", PID)
    with pytest.raises(PersonConflict):
        await store.persons.create("two", request)
    update = PersonUpdate(
        expected_revision=1,
        display_name="新称呼",
        status="inactive",
        profile=PersonProfile(preferred_name="小甲"),
    )
    changed = await store.persons.update("one", PID, update)
    assert changed.person_id == PID and changed.revision == 2
    assert changed.status == "inactive"
    with pytest.raises(PersonConflict):
        await store.persons.update("one", PID, update)
    with pytest.raises(PersonConflict):
        await store.persons.archive("one", PID, 1)
    with pytest.raises(LookupError):
        await store.persons.archive("two", PID, 2)
    await store.persons.archive("one", PID, 2)
    assert await store.persons.list("one") == []


async def test_same_name_does_not_merge_people(store):
    await store.owner_commands.create_owner(owner_id="one")
    for char in ("a", "b"):
        await store.persons.create("one", PersonCreate(person_id=char * 32, display_name="同名"))
    assert len(await store.persons.list("one")) == 2


def test_profile_is_explicit_and_bounded():
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        PersonCreate(person_id=PID, display_name="   ")
    with pytest.raises(ValidationError):
        PersonProfile(inferred_personality="asserted")
