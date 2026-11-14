import base64
import io

import pytest
from eidolon_sdk.biz.person import PersonCreate
from eidolon_sdk.biz.person_face import (
    FaceFeatures,
    FaceRecognitionConsent,
    PersonFaceFeaturesWrite,
    PersonFaceUpdate,
)
from PIL import Image

from eidolon_data.schema.person_faces import PersonFaceRow
from eidolon_data.services.person_faces import PersonFaceInvalid
from eidolon_data.services.persons import PersonConflict

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
    saved = await store.person_faces.replace(
        "one", PID, PersonFaceUpdate(expected_revision=0, image_base64=photo())
    )
    assert saved.has_image and saved.revision == 1 and saved.recognition_state == "not_configured"
    assert saved.model_id is None and saved.feature_schema_version is None
    image = await store.person_faces.image("one", PID)
    assert base64.b64decode(image.image_base64).startswith(b"\xff\xd8")
    with pytest.raises(LookupError):
        await store.person_faces.image("two", PID)
    with pytest.raises(PersonConflict):
        await store.person_faces.replace(
            "one", PID, PersonFaceUpdate(expected_revision=0, image_base64=photo("blue"))
        )
    assert (await store.person_faces.state("one", PID)) == saved
    changed = await store.person_faces.replace(
        "one", PID, PersonFaceUpdate(expected_revision=1, image_base64=photo("blue"))
    )
    assert changed.revision == 2 and changed.sha256 != saved.sha256
    assert len(list(store.object_storage.root.rglob("*.jpg"))) == 1
    cleared = await store.person_faces.delete("one", PID, 2)
    assert not cleared.has_image and cleared.revision == 3
    assert (await store.person_faces.image("one", PID)).image_base64 is None
    assert list(store.object_storage.root.rglob("*.jpg")) == []
    with pytest.raises(PersonConflict):
        await store.person_faces.replace(
            "one", PID, PersonFaceUpdate(expected_revision=0, image_base64=photo())
        )
    assert (await store.persons.memory_state("one", PID))["state"] == "active"


async def test_invalid_photo_and_storage_failure_preserve_existing_photo(store, monkeypatch):
    await setup(store)
    saved = await store.person_faces.replace(
        "one", PID, PersonFaceUpdate(expected_revision=0, image_base64=photo())
    )
    for data in ["not base64", base64.b64encode(b"not an image").decode()]:
        with pytest.raises(PersonFaceInvalid):
            await store.person_faces.replace(
                "one", PID, PersonFaceUpdate(expected_revision=1, image_base64=data)
            )
    from eidolon_data.services.object_storage import LocalObjectStorage

    def fail(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(LocalObjectStorage, "put", fail)
    with pytest.raises(OSError):
        await store.person_faces.replace(
            "one", PID, PersonFaceUpdate(expected_revision=1, image_base64=photo("blue"))
        )
    assert (await store.person_faces.state("one", PID)) == saved
    assert (await store.person_faces.image("one", PID)).image_base64 is not None


async def test_person_deletion_cleans_photo_object_and_metadata(store):
    await setup(store)
    await store.person_faces.replace(
        "one", PID, PersonFaceUpdate(expected_revision=0, image_base64=photo())
    )
    await store.persons.archive("one", PID, 1)
    async with store._session_factory() as session:
        assert await session.get(PersonFaceRow, PID) is None
    assert list(store.object_storage.root.rglob("*.jpg")) == []


async def test_owner_deletion_reports_photo_keys_for_existing_cleanup_pipeline(store):
    await setup(store)
    await store.person_faces.replace(
        "one", PID, PersonFaceUpdate(expected_revision=0, image_base64=photo())
    )
    result = await store.owner_deletion.delete_owner("one")
    assert any(key.startswith("person-faces/") for key in result.object_storage_keys)


async def test_archive_photo_cleanup_failure_keeps_retryable_identity(store, monkeypatch):
    from eidolon_data.services.object_storage import LocalObjectStorage

    await setup(store)
    await store.person_faces.replace(
        "one", PID, PersonFaceUpdate(expected_revision=0, image_base64=photo())
    )
    original_delete = LocalObjectStorage.delete

    def fail(*args, **kwargs):
        raise OSError("temporary removal failure")

    monkeypatch.setattr(LocalObjectStorage, "delete", fail)
    with pytest.raises(OSError):
        await store.persons.archive("one", PID, 1)
    assert (await store.persons.get("one", PID)).status == "active"
    assert (await store.person_faces.state("one", PID)).has_image
    monkeypatch.setattr(LocalObjectStorage, "delete", original_delete)
    await store.persons.archive("one", PID, 1)
    assert list(store.object_storage.root.rglob("*.jpg")) == []


def features(first=0.25):
    return FaceFeatures(model_id="face-model", weights_hash="c" * 64,
                        preprocessing_version="prep-1", vector=[first, 0.5, -0.125])


def write(revision=1, first=0.25, accepted_at=None):
    return PersonFaceFeaturesWrite(
        expected_revision=revision, features=features(first),
        consent=FaceRecognitionConsent(consent_version="face-1", statement="They agreed.",
                                       attested=True, accepted_at=accepted_at))


async def save_photo(store, owner="one", person_id=PID, revision=0, color="red"):
    return await store.person_faces.replace(
        owner, person_id, PersonFaceUpdate(expected_revision=revision, image_base64=photo(color)))


async def face_row(store, person_id=PID):
    async with store._session_factory() as session:
        return await session.get(PersonFaceRow, person_id)


async def test_recognition_is_enabled_only_by_consent_bound_to_the_current_photo(store):
    await setup(store)
    with pytest.raises(PersonConflict):  # no photo yet
        await store.person_faces.enable_recognition("one", PID, write())
    await save_photo(store)
    with pytest.raises(PersonConflict):  # stale photo revision
        await store.person_faces.enable_recognition("one", PID, write(revision=2))
    with pytest.raises(LookupError):
        await store.person_faces.enable_recognition("two", PID, write())
    assert (await store.person_faces.state("one", PID)).recognition_state == "not_configured"

    enabled = await store.person_faces.enable_recognition(
        "one", PID, write(accepted_at="1999-01-01T00:00:00+00:00"))
    assert enabled.recognition_state == "enabled" and enabled.revision == 1
    assert (enabled.model_id, enabled.preprocessing_version, enabled.feature_schema_version,
            enabled.recognition_consent_version) == ("face-model", "prep-1", 1, "face-1")
    # Stamped by Data, not taken from the request.
    assert enabled.recognition_enabled_at.startswith("20")
    assert await store.person_faces.state("one", PID) == enabled
    assert "0.25" not in enabled.model_dump_json() and "vector" not in enabled.model_dump_json()
    row = await face_row(store)
    assert row.features_json == [0.25, 0.5, -0.125] and row.weights_hash == "c" * 64
    assert row.recognition_consent_statement == "They agreed."

    cleared = await store.person_faces.disable_recognition("one", PID)
    assert cleared.recognition_state == "not_configured" and cleared.has_image
    assert cleared.revision == 1 and cleared.model_id is None
    assert cleared.recognition_consent_version is None and cleared.recognition_enabled_at is None
    row = await face_row(store)
    assert (row.features_json, row.weights_hash, row.model_id, row.recognition_consent_version,
            row.recognition_consent_statement, row.recognition_consent_at) == (None,) * 6
    assert await store.person_faces.disable_recognition("one", PID) == cleared

    from sqlalchemy import select

    from eidolon_data.schema.audit import AuditOutboxRow

    async with store._session_factory() as session:
        audits = (await session.scalars(select(AuditOutboxRow))).all()
    actions = [a.action for a in audits]
    # Idempotent: the second disable cleared nothing and recorded nothing.
    assert actions.count("person.face_recognition_enabled") == 1
    assert actions.count("person.face_recognition_disabled") == 1
    for audit in audits:
        assert "0.25" not in repr(audit.payload_json) and "They agreed" not in repr(audit.payload_json)


async def test_inactive_person_cannot_enable_recognition(store):
    from eidolon_sdk.biz.person import PersonUpdate

    await setup(store)
    await save_photo(store)
    person = await store.persons.get("one", PID)
    await store.persons.update("one", PID, PersonUpdate(
        expected_revision=person.revision, display_name="A", status="inactive",
        profile=person.profile))
    with pytest.raises(PersonConflict, match="inactive"):
        await store.person_faces.enable_recognition("one", PID, write())


@pytest.mark.parametrize("change", ["replace", "delete"])
async def test_a_changed_photo_drops_the_feature_and_the_consent(store, change):
    await setup(store)
    await save_photo(store)
    await store.person_faces.enable_recognition("one", PID, write())
    if change == "replace":
        state = await save_photo(store, revision=1, color="blue")
    else:
        state = await store.person_faces.delete("one", PID, 1)
    assert state.recognition_state == "not_configured" and state.revision == 2
    assert state.model_id is None and state.recognition_consent_version is None
    row = await face_row(store)
    assert (row.features_json, row.weights_hash, row.model_id, row.preprocessing_version,
            row.feature_schema_version, row.recognition_consent_version,
            row.recognition_consent_statement, row.recognition_consent_at) == (None,) * 8
    assert (await store.person_faces.recognition_candidates("one")).persons == []
    # The old consent does not carry over to the old revision either.
    with pytest.raises(PersonConflict):
        await store.person_faces.enable_recognition("one", PID, write(revision=1))


async def test_candidates_list_only_enabled_active_persons_of_that_owner(store):
    from eidolon_sdk.biz.person import PersonUpdate

    await setup(store)
    others = {name: name * 32 for name in "bcde"}
    for name, person_id in others.items():
        await store.persons.create("one", PersonCreate(person_id=person_id, display_name=name))
        await save_photo(store, person_id=person_id)
    await store.persons.create("two", PersonCreate(person_id="f" * 32, display_name="F"))
    await save_photo(store, "two", "f" * 32)
    await store.person_faces.enable_recognition("two", "f" * 32, write(first=0.75))
    await save_photo(store)
    empty = await store.person_faces.recognition_candidates("one")
    assert empty.persons == [] and empty.owner_id == "one"

    for person_id in [others["c"], PID, others["d"], others["e"]]:  # b keeps a photo only
        await store.person_faces.enable_recognition("one", person_id, write())
    await store.person_faces.disable_recognition("one", others["c"])
    person = await store.persons.get("one", others["d"])
    await store.persons.update("one", others["d"], PersonUpdate(
        expected_revision=person.revision, display_name="d", status="inactive",
        profile=person.profile))
    await store.persons.archive("one", others["e"], 1)

    listed = await store.person_faces.recognition_candidates("one")
    assert [p.person_id for p in listed.persons] == [PID]
    assert listed.revision != empty.revision
    template = listed.persons[0].templates[0]
    assert listed.persons[0].display_name == "A" and template.revision == 1
    assert template.features.vector == [0.25, 0.5, -0.125]
    assert (await store.person_faces.recognition_candidates("one")).revision == listed.revision
    theirs = await store.person_faces.recognition_candidates("two")
    assert [p.person_id for p in theirs.persons] == ["f" * 32]

    await store.person_faces.enable_recognition("one", PID, write(first=0.5))
    changed = await store.person_faces.recognition_candidates("one")
    assert changed.revision != listed.revision
    assert changed.persons[0].templates[0].features.vector[0] == 0.5
    with pytest.raises(LookupError):
        await store.person_faces.recognition_candidates("nobody")


async def test_candidates_refuse_more_people_than_recognition_compares(store, monkeypatch):
    from eidolon_data.services import person_faces

    await setup(store)
    await store.persons.create("one", PersonCreate(person_id="b" * 32, display_name="B"))
    for person_id in (PID, "b" * 32):
        await save_photo(store, person_id=person_id)
        await store.person_faces.enable_recognition("one", person_id, write())
    monkeypatch.setattr(person_faces, "MAX_FACE_RECOGNITION_PERSONS", 1)
    with pytest.raises(PersonConflict, match="capacity"):
        await store.person_faces.recognition_candidates("one")
