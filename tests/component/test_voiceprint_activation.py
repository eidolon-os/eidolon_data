"""Person lifecycle owns biometric evidence; no Companion activation exists."""

import pytest
from eidolon_sdk.biz.person import PersonProfile, PersonUpdate
from sqlalchemy import select
from test_voiceprints import enroll, setup

from eidolon_data.schema.voiceprints import (
    SpeakerProfileRow,
    SpeakerRecordingRow,
    VoiceEnrollmentRow,
)
from eidolon_data.services.voiceprints import VoiceprintConflict


async def test_person_deletion_erases_biometrics_and_enrollment_evidence(store):
    await setup(store)
    profile = await enroll(store)
    await store.persons.archive("owner", "a" * 32, 1)
    async with store._session_factory() as s:
        assert await s.get(SpeakerProfileRow, profile.profile_id) is None
        assert await s.get(SpeakerRecordingRow, profile.profile_id) is None
        assert list(await s.scalars(select(VoiceEnrollmentRow))) == []


async def test_inactive_person_is_not_a_recognition_candidate(store):
    await setup(store)
    await enroll(store)
    await store.persons.update(
        "owner",
        "a" * 32,
        PersonUpdate(
            expected_revision=1, display_name="甲", status="inactive", profile=PersonProfile()
        ),
    )
    with pytest.raises(VoiceprintConflict, match="inactive"):
        await store.voiceprints.templates("owner", "a" * 32)
    assert len((await store.voiceprints.view("owner")).profiles) == 1
