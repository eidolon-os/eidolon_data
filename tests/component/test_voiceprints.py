"""Owner-scoped enrollment preserves evidence and isolates real people."""

import pytest
from eidolon_sdk.biz.voiceprint import EnrollmentCreate, ExtractedSample

from eidolon_data.services.voiceprints import VoiceprintConflict




def sample(n=0):
    return ExtractedSample(
        model_id="test",
        weights_hash="a" * 64,
        preprocessing_version="v1",
        vector=[1.0, n * 0.01],
        speech_ms=4500,
        quality={"clipping": 0.0},
    )


async def complete(v, owner, eid, trial, *, recording=None):
    import base64
    import hashlib

    from eidolon_sdk.biz.voiceprint import (
        EnrollmentCompletion,
        EnrollmentEvaluation,
        VoiceAudioRequest,
        enrollment_evidence_digest,
    )

    recording = recording or VoiceAudioRequest(
        pcm_base64=base64.b64encode(b"\x01\x00" * 32000).decode()
    )
    state = await v.enrollment(owner, eid)
    samples = {} if state.state == "ready" else (await v.evidence(owner, eid)).samples
    result = EnrollmentCompletion(
        evaluation=EnrollmentEvaluation(
            evidence_digest=enrollment_evidence_digest(samples),
            recording_sha256=hashlib.sha256(base64.b64decode(recording.pcm_base64)).hexdigest(),
            template=next(iter(samples.values()), trial),
            trial=trial,
            registration_min_similarity=1,
            trial_min_similarity=1,
        ),
        recording=recording,
    )
    return await v.complete(owner, eid, result)


async def enroll(store):
    v = store.voiceprints
    e = await v.create_enrollment(
        "owner", EnrollmentCreate(person_id="a" * 32, operation_id="enroll-one", display_name="甲")
    )
    for i in range(3):
        await v.add_sample("owner", e.enrollment_id, str(i), sample(i))
    return await complete(v, "owner", e.enrollment_id, sample(3))


async def test_enrollment_and_owner_isolation(store):
    await setup(store)
    await enroll(store)
    a = await store.voiceprints.view("owner")
    b = await store.voiceprints.view("owner")
    assert a == b
    assert "vector" not in a.model_dump_json()
    with pytest.raises(LookupError):
        await store.voiceprints.view("other")




async def test_idempotent_enrollment_and_failed_replace_preserve_template(store):
    await setup(store)
    p = await enroll(store)
    v = store.voiceprints
    again = await complete(v, "owner", p.enrollment_id, sample(3))
    assert again.profile_id == p.profile_id
    e = await v.create_enrollment(
        "owner",
        EnrollmentCreate(person_id="a" * 32, operation_id="replace", display_name="甲", profile_id=p.profile_id),
    )
    await v.add_sample("owner", e.enrollment_id, "0", sample())
    with pytest.raises(VoiceprintConflict):
        await complete(v, "owner", e.enrollment_id, sample())
    assert len((await v.view("owner")).profiles) == 1


async def test_sample_identity_validation(store):
    await setup(store)
    with pytest.raises(ValueError):
        ExtractedSample(
            model_id="test",
            weights_hash="a" * 64,
            preprocessing_version="v1",
            vector=[float("nan"), 0],
            speech_ms=4000,
        )


async def test_delete_is_retryable_and_removes_templates(store):
    await setup(store)
    p = await enroll(store)
    v = store.voiceprints
    await v.delete_profile("owner", p.profile_id)
    again = await v.delete_profile("owner", p.profile_id)
    assert again.profiles == []


async def test_expired_samples_are_purged_without_mobile_returning(store):
    from unittest.mock import patch

    await setup(store)
    v = store.voiceprints
    e = await v.create_enrollment(
        "owner", EnrollmentCreate(person_id="a" * 32, operation_id="expire", display_name="甲")
    )
    await v.add_sample("owner", e.enrollment_id, "0", sample())
    with patch("eidolon_data.services.voiceprints.time.time", return_value=e.expires_at + 1):
        assert await v.expire_enrollments() == 1
    result = await v.enrollment("owner", e.enrollment_id)
    assert result.state == "expired"
    assert result.accepted_samples == 0


async def test_reference_recording_is_atomic_owner_scoped_and_deleted_with_profile(store):
    import base64

    from eidolon_sdk.biz.voiceprint import VoiceAudioRequest

    from eidolon_data.schema.voiceprints import SpeakerRecordingRow

    await setup(store)
    v = store.voiceprints
    e = await v.create_enrollment(
        "owner", EnrollmentCreate(person_id="a" * 32, operation_id="audio", display_name="甲")
    )
    for i in range(3):
        await v.add_sample("owner", e.enrollment_id, str(i), sample(i))
    pcm = b"\x01\x00" * 32000
    recording = VoiceAudioRequest(pcm_base64=base64.b64encode(pcm).decode())
    p = await complete(v, "owner", e.enrollment_id, sample(), recording=recording)
    assert p.recording_available
    assert (await v.view("owner")).profiles[0].recording_available
    assert (await v.recording("owner", p.profile_id)).pcm_base64 == recording.pcm_base64
    with pytest.raises(LookupError):
        await v.recording("intruder", p.profile_id)
    assert "pcm_base64" not in (await v.templates("owner", "a" * 32)).model_dump_json()
    replacement = await v.create_enrollment(
        "owner",
        EnrollmentCreate(person_id="a" * 32, operation_id="replace-audio", display_name="甲", profile_id=p.profile_id),
    )
    with pytest.raises(VoiceprintConflict):
        await complete(
            v,
            "owner",
            replacement.enrollment_id,
            sample(),
            recording=VoiceAudioRequest(pcm_base64=base64.b64encode(b"\x02\x00" * 32000).decode()),
        )
    assert (await v.recording("owner", p.profile_id)).pcm_base64 == recording.pcm_base64
    for i in range(3):
        await v.add_sample("owner", replacement.enrollment_id, str(i), sample(i))
    newer = VoiceAudioRequest(pcm_base64=base64.b64encode(b"\x02\x00" * 32000).decode())
    await complete(v, "owner", replacement.enrollment_id, sample(), recording=newer)
    assert (await v.recording("owner", p.profile_id)).pcm_base64 == newer.pcm_base64
    # A network retry cannot replace the recording of an already completed operation.
    await complete(v, "owner", replacement.enrollment_id, sample(), recording=recording)
    assert (await v.recording("owner", p.profile_id)).pcm_base64 == newer.pcm_base64
    assert (await v.recording("owner", p.profile_id)).pcm_base64 == newer.pcm_base64
    await v.delete_profile("owner", p.profile_id)
    async with store._session_factory() as s:
        assert await s.get(SpeakerRecordingRow, p.profile_id) is None


async def test_owned_profile_has_no_fabricated_recording(store):
    await setup(store)
    from eidolon_data.schema.voiceprints import SpeakerProfileRow

    async with store._session_factory() as s, s.begin():
        s.add(
            SpeakerProfileRow(
                profile_id="legacy",
                owner_id="owner",
                person_id="a" * 32,
                display_name="旧档案",
                revision=1,
                enrollment_id="old",
                template_json=sample().model_dump(),
            )
        )
    p = (await store.voiceprints.view("owner")).profiles[0]
    assert not p.recording_available
    with pytest.raises(LookupError):
        await store.voiceprints.recording("owner", p.profile_id)


async def setup(store):
    from eidolon_sdk.biz.person import PersonCreate
    await store.owner_commands.create_owner(owner_id="owner")
    await store.persons.create("owner", PersonCreate(person_id="a" * 32, display_name="甲"))


async def test_recognition_snapshot_changes_on_lifecycle_and_excludes_other_owner(store):
    from eidolon_sdk.biz.person import PersonUpdate, PersonProfile
    await setup(store)
    profile=await enroll(store)
    snapshot=await store.voiceprints.recognition_candidates('owner')
    assert len(snapshot.persons)==1
    assert snapshot.persons[0].person_id=='a'*32
    assert snapshot.persons[0].templates[0].profile_id==profile.profile_id
    assert 'pcm_base64' not in snapshot.model_dump_json()
    await store.owner_commands.create_owner(owner_id='second-owner')
    assert (await store.voiceprints.recognition_candidates('second-owner')).persons==[]
    changed=await store.persons.update('owner','a'*32,PersonUpdate(expected_revision=1,
        display_name='new name',status='active',profile=PersonProfile(preferred_name='new')))
    after=await store.voiceprints.recognition_candidates('owner')
    assert after.revision!=snapshot.revision and after.persons[0].person_revision==changed.revision
    await store.persons.update('owner','a'*32,PersonUpdate(expected_revision=changed.revision,
        display_name='new name',status='inactive',profile=PersonProfile()))
    assert (await store.voiceprints.recognition_candidates('owner')).persons==[]


async def test_new_enrollment_replaces_single_profile_and_rejects_concurrent_stale_completion(store):
    await setup(store)
    original = await enroll(store)
    v = store.voiceprints
    sessions = []
    for op in ("replacement-a", "replacement-b"):
        e = await v.create_enrollment("owner", EnrollmentCreate(person_id="a" * 32, operation_id=op, display_name="甲"))
        assert e.profile_id == original.profile_id
        for i in range(3): await v.add_sample("owner", e.enrollment_id, str(i), sample(i))
        sessions.append(e)
    updated = await complete(v, "owner", sessions[0].enrollment_id, sample(3))
    assert updated.profile_id == original.profile_id and updated.revision == original.revision + 1
    with pytest.raises(VoiceprintConflict):
        await complete(v, "owner", sessions[1].enrollment_id, sample(3))
    profiles = (await v.view("owner")).profiles
    assert len(profiles) == 1 and profiles[0].revision == updated.revision
