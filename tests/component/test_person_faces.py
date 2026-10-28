import base64
import io
import pytest
from PIL import Image
from eidolon_sdk.biz.person import PersonCreate
from eidolon_sdk.biz.person_face import PersonFaceUpdate
from eidolon_data.services.persons import PersonConflict
from eidolon_data.services.person_faces import PersonFaceInvalid
from eidolon_data.schema.person_faces import PersonFaceRow

PID = "a" * 32

def photo(color="red"):
    out = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(out, format="PNG")
    return base64.b64encode(out.getvalue()).decode()

async def setup(store):
    await store.owner_commands.create_owner(owner_id="one")
    await store.owner_commands.create_owner(owner_id="two")
    await store.persons.create("one", PersonCreate(person_id=PID, display_name="A"))

async def test_photo_crud_is_versioned_owner_scoped_and_not_recognition(store):
    await setup(store)
    assert not (await store.person_faces.state("one", PID)).has_image
    saved = await store.person_faces.replace("one", PID, PersonFaceUpdate(expected_revision=0, image_base64=photo()))
    assert saved.has_image and saved.revision == 1 and saved.recognition_state == "not_configured"
    assert saved.model_id is None and saved.feature_schema_version is None
    image = await store.person_faces.image("one", PID)
    assert base64.b64decode(image.image_base64).startswith(b"\xff\xd8")
    with pytest.raises(LookupError):
        await store.person_faces.image("two", PID)
    with pytest.raises(PersonConflict):
        await store.person_faces.replace("one", PID, PersonFaceUpdate(expected_revision=0, image_base64=photo("blue")))
    assert (await store.person_faces.state("one", PID)) == saved
    changed = await store.person_faces.replace("one", PID, PersonFaceUpdate(expected_revision=1, image_base64=photo("blue")))
    assert changed.revision == 2 and changed.sha256 != saved.sha256
    assert len(list(store.object_storage.root.rglob("*.jpg"))) == 1
    cleared = await store.person_faces.delete("one", PID, 2)
    assert not cleared.has_image and cleared.revision == 3
    assert (await store.person_faces.image("one", PID)).image_base64 is None
    assert list(store.object_storage.root.rglob("*.jpg")) == []
    with pytest.raises(PersonConflict):
        await store.person_faces.replace("one", PID, PersonFaceUpdate(expected_revision=0, image_base64=photo()))
    assert (await store.persons.memory_state("one", PID))["state"] == "active"

async def test_invalid_photo_and_storage_failure_preserve_existing_photo(store, monkeypatch):
    await setup(store)
    saved = await store.person_faces.replace("one", PID, PersonFaceUpdate(expected_revision=0, image_base64=photo()))
    for data in ["not base64", base64.b64encode(b"not an image").decode()]:
        with pytest.raises(PersonFaceInvalid):
            await store.person_faces.replace("one", PID, PersonFaceUpdate(expected_revision=1, image_base64=data))
    from eidolon_data.services.object_storage import LocalObjectStorage
    def fail(*args, **kwargs): raise OSError("disk full")
    monkeypatch.setattr(LocalObjectStorage, "put", fail)
    with pytest.raises(OSError):
        await store.person_faces.replace("one", PID, PersonFaceUpdate(expected_revision=1, image_base64=photo("blue")))
    assert (await store.person_faces.state("one", PID)) == saved
    assert (await store.person_faces.image("one", PID)).image_base64 is not None

async def test_person_deletion_cleans_photo_object_and_metadata(store):
    await setup(store)
    await store.person_faces.replace("one", PID, PersonFaceUpdate(expected_revision=0, image_base64=photo()))
    await store.persons.delete("one", PID, 1)
    async with store._session_factory() as session:
        assert await session.get(PersonFaceRow, PID) is None
    assert list(store.object_storage.root.rglob("*.jpg")) == []

async def test_owner_deletion_reports_photo_keys_for_existing_cleanup_pipeline(store):
    await setup(store)
    await store.person_faces.replace("one", PID, PersonFaceUpdate(expected_revision=0, image_base64=photo()))
    result = await store.owner_deletion.delete_owner("one")
    assert any(key.startswith("person-faces/") for key in result.object_storage_keys)
