"""Voice admission is Companion scoped and mutations invalidate old evidence."""

import pytest

from eidolon_sdk.biz.voiceprint import EnrollmentCreate, ExtractedSample, PolicyUpdate
from eidolon_data.services.voiceprints import VoiceprintConflict


async def setup(store):
    await store.owner_commands.create_owner(owner_id="owner")
    for name in ("a", "b"):
        await store.companion_workspaces.provision_workspace(
            owner_id="owner",
            companion_id=name,
            genome_id=f"g-{name}",
            realm_id="realm" if name == "a" else None,
            kind="conversational",
        )


def sample(n=0):
    return ExtractedSample(
        model_id="test",
        weights_hash="a" * 64,
        preprocessing_version="v1",
        vector=[1.0, n * 0.01],
        speech_ms=4500,
        quality={"clipping": 0.0},
    )


async def enroll(store, companion="a"):
    v = store.voiceprints
    e = await v.create_enrollment(
        "owner", companion, EnrollmentCreate(operation_id="enroll-one", display_name="甲")
    )
    for i in range(3):
        await v.add_sample("owner", companion, e.enrollment_id, str(i), sample(i))
    return await v.complete("owner", companion, e.enrollment_id, sample(3))


async def test_enrollment_and_companion_isolation(store):
    await setup(store)
    profile = await enroll(store)
    a = await store.voiceprints.view("owner", "a")
    b = await store.voiceprints.view("owner", "b")
    assert a.allowed_profile_ids == [profile.profile_id]
    assert b.allowed_profile_ids == []
    assert "vector" not in a.model_dump_json()
    with pytest.raises(LookupError):
        await store.voiceprints.view("other", "a")


async def test_stale_evidence_cannot_be_admitted_after_removal(store):
    await setup(store)
    p = await enroll(store)
    v = store.voiceprints
    view = await v.view("owner", "a")
    await v.set_policy(
        "owner", "a", PolicyUpdate(mode="allowlist", expected_revision=view.revision)
    )
    snapshot = await v.snapshot("a")
    assert await v.admit("a", snapshot.revision, p.profile_id)
    await v.unbind("owner", "a", p.profile_id)
    assert not await v.admit("a", snapshot.revision, p.profile_id)
    assert (await v.view("owner", "a")).mode == "allowlist"


async def test_idempotent_enrollment_and_failed_replace_preserve_template(store):
    await setup(store)
    p = await enroll(store)
    v = store.voiceprints
    again = await v.complete("owner", "a", p.enrollment_id, sample(3))
    assert again.profile_id == p.profile_id
    e = await v.create_enrollment(
        "owner",
        "a",
        EnrollmentCreate(operation_id="replace", display_name="甲", profile_id=p.profile_id),
    )
    await v.add_sample("owner", "a", e.enrollment_id, "0", sample())
    with pytest.raises(VoiceprintConflict):
        await v.complete("owner", "a", e.enrollment_id, sample())
    assert len((await v.snapshot("a")).templates) == 1


async def test_policy_cas_and_sample_identity_validation(store):
    await setup(store)
    v = store.voiceprints
    await v.set_policy("owner", "a", PolicyUpdate(mode="allowlist", expected_revision=0))
    with pytest.raises(VoiceprintConflict):
        await v.set_policy("owner", "a", PolicyUpdate(mode="off", expected_revision=0))
    with pytest.raises(ValueError):
        ExtractedSample(
            model_id="test",
            weights_hash="a" * 64,
            preprocessing_version="v1",
            vector=[float("nan"), 0],
            speech_ms=4000,
        )


async def test_delete_is_retryable_and_revokes_every_companion(store):
    await setup(store)
    p = await enroll(store)
    v = store.voiceprints
    await v.bind("owner", "b", p.profile_id)
    before = (await v.snapshot("b")).revision
    await v.delete_profile("owner", "a", p.profile_id)
    again = await v.delete_profile("owner", "a", p.profile_id)
    assert again.profiles == []
    assert (await v.snapshot("b")).revision > before
    assert (await v.snapshot("b")).templates == []


async def test_expired_samples_are_purged_without_mobile_returning(store):
    from unittest.mock import patch

    await setup(store)
    v = store.voiceprints
    e = await v.create_enrollment(
        "owner", "a", EnrollmentCreate(operation_id="expire", display_name="甲")
    )
    await v.add_sample("owner", "a", e.enrollment_id, "0", sample())
    with patch("eidolon_data.services.voiceprints.time.time", return_value=e.expires_at + 1):
        assert await v.expire_enrollments() == 1
    result = await v.enrollment("owner", "a", e.enrollment_id)
    assert result.state == "expired"
    assert result.accepted_samples == 0


async def test_reference_recording_is_atomic_owner_scoped_and_deleted_with_profile(store):
    from eidolon_sdk.biz.voiceprint import VoiceAudioRequest
    from eidolon_data.schema.voiceprints import SpeakerRecordingRow
    import base64
    await setup(store)
    v = store.voiceprints
    e = await v.create_enrollment('owner', 'a', EnrollmentCreate(operation_id='audio', display_name='甲'))
    for i in range(3):
        await v.add_sample('owner', 'a', e.enrollment_id, str(i), sample(i))
    pcm = b'\x01\x00' * 32000
    recording = VoiceAudioRequest(pcm_base64=base64.b64encode(pcm).decode())
    p = await v.complete('owner', 'a', e.enrollment_id, sample(), recording=recording)
    assert p.recording_available
    assert (await v.view('owner', 'a')).profiles[0].recording_available
    assert (await v.recording('owner', 'a', p.profile_id)).pcm_base64 == recording.pcm_base64
    with pytest.raises(LookupError):
        await v.recording('intruder', 'a', p.profile_id)
    assert 'pcm_base64' not in (await v.snapshot('a')).model_dump_json()
    replacement = await v.create_enrollment('owner', 'a', EnrollmentCreate(operation_id='replace-audio', display_name='甲', profile_id=p.profile_id))
    with pytest.raises(VoiceprintConflict):
        await v.complete('owner', 'a', replacement.enrollment_id, sample(), recording=VoiceAudioRequest(pcm_base64=base64.b64encode(b'\x02\x00'*32000).decode()))
    assert (await v.recording('owner', 'a', p.profile_id)).pcm_base64 == recording.pcm_base64
    for i in range(3):
        await v.add_sample('owner', 'a', replacement.enrollment_id, str(i), sample(i))
    newer = VoiceAudioRequest(pcm_base64=base64.b64encode(b'\x02\x00'*32000).decode())
    await v.complete('owner', 'a', replacement.enrollment_id, sample(), recording=newer)
    assert (await v.recording('owner', 'a', p.profile_id)).pcm_base64 == newer.pcm_base64
    # A network retry cannot replace the recording of an already completed operation.
    await v.complete('owner', 'a', replacement.enrollment_id, sample(), recording=recording)
    assert (await v.recording('owner', 'a', p.profile_id)).pcm_base64 == newer.pcm_base64
    await v.unbind('owner', 'a', p.profile_id)
    assert (await v.recording('owner', 'a', p.profile_id)).pcm_base64 == newer.pcm_base64
    await v.delete_profile('owner', 'a', p.profile_id)
    async with store._session_factory() as s:
        assert await s.get(SpeakerRecordingRow, p.profile_id) is None


async def test_legacy_profile_has_no_fabricated_recording(store):
    await setup(store)
    p = await enroll(store)
    assert not p.recording_available
    with pytest.raises(LookupError):
        await store.voiceprints.recording('owner', 'a', p.profile_id)
