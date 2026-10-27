"""Person lifecycle owns biometric evidence; no Companion activation exists."""
import pytest
from sqlalchemy import select
from eidolon_sdk.biz.person import PersonUpdate, PersonProfile
from eidolon_sdk.biz.voiceprint import VoiceprintAssignment
from eidolon_data.schema.voiceprints import SpeakerProfileRow, SpeakerRecordingRow, VoiceEnrollmentRow
from eidolon_data.services.voiceprints import VoiceprintConflict
from test_voiceprints import setup, enroll, sample

async def test_person_deletion_erases_biometrics_and_enrollment_evidence(store):
    await setup(store)
    profile = await enroll(store)
    await store.persons.delete("owner", "a" * 32, 1)
    async with store._session_factory() as s:
        assert await s.get(SpeakerProfileRow, profile.profile_id) is None
        assert await s.get(SpeakerRecordingRow, profile.profile_id) is None
        assert list(await s.scalars(select(VoiceEnrollmentRow))) == []

async def test_inactive_person_is_not_a_recognition_candidate(store):
    await setup(store)
    await enroll(store)
    await store.persons.update("owner", "a" * 32, PersonUpdate(expected_revision=1,
        display_name="甲", status="inactive", profile=PersonProfile()))
    with pytest.raises(VoiceprintConflict, match="inactive"):
        await store.voiceprints.templates("owner", "a" * 32)
    assert len((await store.voiceprints.view("owner")).profiles) == 1

async def test_legacy_assignment_is_explicit_revisioned_and_owner_scoped(store):
    await setup(store)
    async with store._session_factory() as s, s.begin():
        s.add(SpeakerProfileRow(profile_id="legacy", owner_id="owner", display_name="甲",
            revision=4, enrollment_id="old", template_json=sample().model_dump()))
    assert (await store.voiceprints.templates("owner", "a" * 32)).templates == []
    req = VoiceprintAssignment(person_id="a" * 32, expected_revision=4)
    with pytest.raises(LookupError):
        await store.voiceprints.assign("other", "legacy", req)
    linked = await store.voiceprints.assign("owner", "legacy", req)
    assert linked.revision == 5 and linked.person_id == "a" * 32
    with pytest.raises(VoiceprintConflict):
        await store.voiceprints.assign("owner", "legacy", req)
    assert len((await store.voiceprints.templates("owner", "a" * 32)).templates) == 1
