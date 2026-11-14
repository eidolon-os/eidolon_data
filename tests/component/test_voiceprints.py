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


async def complete(v, owner, eid, trial, *, recording=None, calibration=None):
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
            calibration=calibration,
        ),
        recording=recording,
    )
    return await v.complete(owner, eid, result)


async def enroll(store):
    v = store.voiceprints
    e = await v.create_enrollment(
        "owner", EnrollmentCreate(person_id="a" * 32, operation_id="enroll-one")
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
        EnrollmentCreate(person_id="a" * 32, operation_id="replace", profile_id=p.profile_id),
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
        "owner", EnrollmentCreate(person_id="a" * 32, operation_id="expire")
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
        "owner", EnrollmentCreate(person_id="a" * 32, operation_id="audio")
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
        EnrollmentCreate(person_id="a" * 32, operation_id="replace-audio", profile_id=p.profile_id),
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
    assert snapshot.persons[0].maturity == 'established'
    assert snapshot.persons[0].is_named is True
    assert snapshot.persons[0].templates[0].profile_id==profile.profile_id
    assert 'pcm_base64' not in snapshot.model_dump_json()
    await store.owner_commands.create_owner(owner_id='second-owner')
    assert (await store.voiceprints.recognition_candidates('second-owner')).persons==[]
    changed=await store.persons.update('owner','a'*32,PersonUpdate(expected_revision=1,
        display_name='new name',is_named=False,status='active',profile=PersonProfile(preferred_name='new')))
    after=await store.voiceprints.recognition_candidates('owner')
    assert after.persons[0].is_named is False
    assert after.revision!=snapshot.revision and after.persons[0].person_revision==changed.revision
    await store.persons.update('owner','a'*32,PersonUpdate(expected_revision=changed.revision,
        display_name='new name',status='inactive',profile=PersonProfile()))
    assert (await store.voiceprints.recognition_candidates('owner')).persons==[]


async def test_a_voice_candidate_is_addressed_as_the_person_asked_to_be_called(store):
    """Heard or seen, one person has one name: the SDK's ``address_as``, which
    face candidates carry too. The voiceprint keeps no name of its own."""
    from eidolon_sdk.biz.person import PersonProfile, PersonUpdate
    await setup(store)
    profile = await enroll(store)
    assert "display_name" not in profile.model_dump()

    async def listed():
        snapshot = await store.voiceprints.recognition_candidates("owner")
        return snapshot.persons[0], snapshot.revision

    async def rename(display_name, preferred_name):
        person = await store.persons.get("owner", "a" * 32)
        await store.persons.update("owner", "a" * 32, PersonUpdate(
            expected_revision=person.revision, display_name=display_name, status="active",
            profile=PersonProfile(preferred_name=preferred_name)))
        return await listed()

    candidate, revision = await listed()
    assert candidate.address_as == "甲" and "display_name" not in candidate.model_dump()
    called, after_preference = await rename("甲", "老甲")
    assert called.address_as == "老甲" and called.profile.preferred_name == "老甲"
    assert after_preference != revision
    # Relisting leaves the spoken name alone; the snapshot still moves with the Person's revision.
    relisted, after_listing = await rename("乙", "老甲")
    assert relisted.address_as == "老甲" and after_listing != after_preference
    fallback, after_clearing = await rename("乙", "  ")
    assert fallback.address_as == "乙" and after_clearing != after_listing


async def test_new_enrollment_replaces_single_profile_and_rejects_concurrent_stale_completion(store):
    await setup(store)
    original = await enroll(store)
    v = store.voiceprints
    sessions = []
    for op in ("replacement-a", "replacement-b"):
        e = await v.create_enrollment("owner", EnrollmentCreate(person_id="a" * 32, operation_id=op))
        assert e.profile_id == original.profile_id
        for i in range(3): await v.add_sample("owner", e.enrollment_id, str(i), sample(i))
        sessions.append(e)
    updated = await complete(v, "owner", sessions[0].enrollment_id, sample(3))
    assert updated.profile_id == original.profile_id and updated.revision == original.revision + 1
    with pytest.raises(VoiceprintConflict):
        await complete(v, "owner", sessions[1].enrollment_id, sample(3))
    profiles = (await v.view("owner")).profiles
    assert len(profiles) == 1 and profiles[0].revision == updated.revision


async def test_candidate_pool_exit_retains_identity_recording_and_realm(store):
    from eidolon_sdk.biz.person import PersonCreate, PersonUpdate
    from eidolon_data.services.persons import PersonConflict

    await store.owner_commands.create_owner(owner_id='owner')
    person = await store.persons.create('owner', PersonCreate(
        person_id='a' * 32, display_name='甲', maturity='candidate', is_named=False))
    profile = await enroll(store)
    # Enrollment may update Person revision; retirement must use fresh authority.
    person = await store.persons.get('owner', person.person_id)
    before = await store.voiceprints.recognition_candidates('owner')
    recording = await store.voiceprints.recording('owner', profile.profile_id)
    realm = await store.persons.memory_state('owner', person.person_id)
    retired = await store.persons.retire_candidate('owner', person.person_id, person.revision)
    assert retired.recognition_state == 'retained' and retired.status == person.status
    assert retired.person_id == person.person_id and retired.revision == person.revision + 1
    after = await store.voiceprints.recognition_candidates('owner')
    assert not after.persons and after.revision != before.revision
    assert (await store.voiceprints.view('owner')).profiles[0].profile_id == profile.profile_id
    assert await store.voiceprints.recording('owner', profile.profile_id) == recording
    assert (await store.persons.memory_state('owner', person.person_id))['realm_id'] == realm['realm_id']
    assert len(await store.persons.list('owner')) == 1
    assert await store.persons.list('owner', deleted=True) == []
    with pytest.raises(PersonConflict):
        await store.persons.retire_candidate('owner', person.person_id, person.revision)
    assert await store.persons.retire_candidate('owner', person.person_id, retired.revision) == retired
    # Ordinary naming/activation cannot silently put a retained candidate back.
    await store.persons.update('owner', person.person_id, PersonUpdate(
        expected_revision=retired.revision, display_name='new', status='active', profile=person.profile))
    assert not (await store.voiceprints.recognition_candidates('owner')).persons


IDENTITY_POLICY = 'identity-trial'


async def candidate_evidence_setup(store):
    import time

    from eidolon_sdk.biz.candidate_evidence import CandidateEvidence
    from eidolon_sdk.biz.person import PersonCreate
    from eidolon_sdk.biz.speaker_identity import (
        IdentityDecisions,
        PersonEvidence,
        SpeakerEvidenceSource,
        SpeakerIdentityObservation,
    )
    from eidolon_sdk.biz.voiceprint import SpeakerAudioQuality
    await store.owner_commands.create_owner(owner_id='owner')
    await store.persons.create('owner', PersonCreate(person_id='a' * 32,
        display_name='匿名', maturity='candidate', is_named=False))
    profile = await enroll(store)
    snapshot = await store.voiceprints.recognition_candidates('owner')
    person = snapshot.persons[0]
    now = time.time()
    observation = SpeakerIdentityObservation(status='compared', owner_id='owner',
        source=SpeakerEvidenceSource(request_id='t', session_id='s', input_id='mic',
            generation=1, sample_rate=16000, start_sample=0, end_sample=32000, channel='esp-box-3'),
        session_id='s', turn_id='t', issued_at=now, expires_at=now + 10,
        model_id='test', weights_hash='a' * 64, preprocessing_version='v1',
        backend_version='identity-backend-v1', policy_version=IDENTITY_POLICY, reason='compared',
        evidence=[PersonEvidence(person_id=person.person_id, person_revision=person.person_revision,
            maturity='candidate', is_named=False, profile_id=profile.profile_id,
            profile_revision=profile.revision, llr=6.0, posterior=0.98)],
        posterior_unknown=0.02,
        decisions=IdentityDecisions(candidate_evidence=person.person_id),
        audio_quality=SpeakerAudioQuality(audio_ms=2000, speech_ms=2000, required_speech_ms=1500, overlap_detected=0))
    return CandidateEvidence(snapshot_revision=snapshot.revision, audio_sha256='c' * 64, observation=observation)


def chosen(request):
    return request.observation.decided('candidate_evidence')


def another_candidate_turn(request, n, *, overlap=False, **decided):
    from eidolon_sdk.biz.candidate_evidence import CandidateEvidence
    payload = request.model_dump()
    payload['audio_sha256'] = f'{n:064x}'
    value = payload['observation']
    value['turn_id'] = value['source']['request_id'] = f't{n}'
    value['source']['start_sample'] = 1 if overlap else n * 32000
    value['source']['end_sample'] = 32001 if overlap else (n + 1) * 32000
    value['evidence'][0].update(decided)
    return CandidateEvidence.model_validate(payload)


async def test_candidate_receipts_deduplicate_and_do_not_promote_or_grant(store):
    import asyncio
    request = await candidate_evidence_setup(store)
    first, second = await asyncio.gather(
        store.voiceprints.record_candidate_evidence('owner', request),
        store.voiceprints.record_candidate_evidence('owner', request))
    assert sorted([first.accepted, second.accepted]) == [False, True]
    assert first.independent_turns == second.independent_turns == 1
    for n in range(1, 5):
        result = await store.voiceprints.record_candidate_evidence('owner', another_candidate_turn(request, n))
        assert result.independent_turns == n + 1
    with pytest.raises(VoiceprintConflict, match='capacity'):
        await store.voiceprints.record_candidate_evidence('owner', another_candidate_turn(request, 5))
    person = await store.persons.get('owner', 'a' * 32)
    assert person.maturity == 'candidate' and person.revision == chosen(request).person_revision
    assert (await store.persons.memory_state('owner', person.person_id))['state'] == 'inactive'


async def test_candidate_receipts_reject_replay_overlap_stale_scope_and_retirement(store):
    request = await candidate_evidence_setup(store)
    await store.voiceprints.record_candidate_evidence('owner', request)
    for changed, reason in [
        (another_candidate_turn(request, 1, overlap=True), 'overlapping'),
        (another_candidate_turn(request, 1).model_copy(update={'audio_sha256': request.audio_sha256}), 'already counted'),
        (another_candidate_turn(request, 1).model_copy(update={'snapshot_revision': 'f' * 64}), 'snapshot'),
        # The decided candidate must be the current Person and template revision.
        (another_candidate_turn(request, 1, person_revision=chosen(request).person_revision + 1), 'snapshot'),
        (another_candidate_turn(request, 1, profile_revision=chosen(request).profile_revision + 1), 'template'),
    ]:
        with pytest.raises(VoiceprintConflict, match=reason):
            await store.voiceprints.record_candidate_evidence('owner', changed)
    with pytest.raises(VoiceprintConflict, match='owner'):
        await store.voiceprints.record_candidate_evidence('other', request)
    await store.persons.retire_candidate('owner', 'a' * 32, chosen(request).person_revision)
    with pytest.raises(VoiceprintConflict, match='not accepting'):
        await store.voiceprints.record_candidate_evidence('owner', request)


async def test_candidate_receipt_api_requires_service_authority(store):
    import httpx
    from fastapi import FastAPI
    from eidolon_data.api.routers.voiceprints import create_voiceprint_router
    request = await candidate_evidence_setup(store)
    app = FastAPI()
    app.include_router(create_voiceprint_router(store, 'secret'))
    path = '/api/companion-authority/v1/owners/owner/voiceprints/candidate-evidence'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post(path, json=request.model_dump(mode='json'))).status_code == 401
        response = await client.post(path, json=request.model_dump(mode='json'), headers={'Authorization': 'Bearer secret'})
        assert response.status_code == 200 and response.json()['independent_turns'] == 1


def promotion_policy(**changes):
    from eidolon_sdk.biz.candidate_evidence import CandidatePromotionPolicy
    return CandidatePromotionPolicy(**(dict(version='promotion-trial-v1', identity_policy_version=IDENTITY_POLICY,
        model_id='test', weights_hash='a' * 64, preprocessing_version='v1', min_posterior=0.95) | changes))


async def test_configured_promotion_is_atomic_stable_and_does_not_grant_memory(store):
    store.settings.candidate_promotion = promotion_policy()
    request = await candidate_evidence_setup(store)
    original = await store.persons.memory_state('owner', 'a' * 32)
    for n in range(3):
        current = request if n == 0 else another_candidate_turn(request, n)
        result = await store.voiceprints.record_candidate_evidence('owner', current)
        assert result.maturity == ('candidate' if n < 2 else 'established')
    person = await store.persons.get('owner', 'a' * 32)
    assert person.person_id == chosen(request).person_id and person.is_named is False
    assert person.revision == chosen(request).person_revision + 1
    assert result.person_revision == person.revision
    replay = await store.voiceprints.record_candidate_evidence('owner', current)
    assert not replay.accepted and replay.maturity == 'established'
    assert replay.person_revision == person.revision
    state = await store.persons.memory_state('owner', person.person_id)
    assert state['realm_id'] == original['realm_id'] and state['state'] == 'inactive'
    from eidolon_data.services.persons import PersonConflict
    with pytest.raises(PersonConflict, match='inactive'):
        await store.persons.memory_realm('owner', person.person_id)
    assert (await store.voiceprints.recognition_candidates('owner')).revision != request.snapshot_revision


@pytest.mark.parametrize('policy_change', [
    {'identity_policy_version': 'other'}, {'model_id': 'other'},
    {'min_posterior': 0.99}, {'min_total_speech_ms': 9000},
])
async def test_promotion_requires_matching_policy_and_sufficient_evidence(store, policy_change):
    store.settings.candidate_promotion = promotion_policy(**policy_change)
    request = await candidate_evidence_setup(store)
    for n in range(3):
        await store.voiceprints.record_candidate_evidence('owner', request if n == 0 else another_candidate_turn(request, n))
    assert (await store.persons.get('owner', 'a' * 32)).maturity == 'candidate'


async def test_promotion_does_not_mix_identity_policies(store):
    store.settings.candidate_promotion = promotion_policy()
    request = await candidate_evidence_setup(store)
    older = request.model_copy(deep=True)
    older.observation.policy_version = 'older-identity'
    await store.voiceprints.record_candidate_evidence('owner', older)
    for n in (1, 2):
        result = await store.voiceprints.record_candidate_evidence('owner', another_candidate_turn(request, n))
        assert result.maturity == 'candidate'
    result = await store.voiceprints.record_candidate_evidence('owner', another_candidate_turn(request, 3))
    assert result.maturity == 'established' and result.independent_turns == 3


async def test_candidate_expiry_retains_evidence_and_duplicate_does_not_extend_pool(store, monkeypatch):
    import time
    clock = [time.time()]
    monkeypatch.setattr('eidolon_data.services.voiceprints.time.time', lambda: clock[0])
    store.settings.candidate_pool_ttl_s = 60
    request = await candidate_evidence_setup(store)
    await store.voiceprints.record_candidate_evidence('owner', request)
    profiles = await store.voiceprints.view('owner')
    recording = await store.voiceprints.recording('owner', chosen(request).profile_id)
    realm = await store.persons.memory_state('owner', 'a' * 32)
    clock[0] += 50
    assert not (await store.voiceprints.record_candidate_evidence('owner', request)).accepted
    clock[0] += 11
    assert not (await store.voiceprints.recognition_candidates('owner')).persons
    person = await store.persons.get('owner', 'a' * 32)
    assert person.recognition_state == 'retained' and person.status == 'active'
    assert person.revision == chosen(request).person_revision + 1
    assert await store.voiceprints.view('owner') == profiles
    assert await store.voiceprints.recording('owner', chosen(request).profile_id) == recording
    assert (await store.persons.memory_state('owner', 'a' * 32))['realm_id'] == realm['realm_id']
    assert not (await store.voiceprints.recognition_candidates('owner')).persons
    assert (await store.persons.get('owner', 'a' * 32)).revision == person.revision


async def test_only_new_independent_evidence_extends_candidate_pool(store, monkeypatch):
    import time
    clock = [time.time()]
    monkeypatch.setattr('eidolon_data.services.voiceprints.time.time', lambda: clock[0])
    store.settings.candidate_pool_ttl_s = 60
    request = await candidate_evidence_setup(store)
    await store.voiceprints.record_candidate_evidence('owner', request)
    clock[0] += 50
    fresh = another_candidate_turn(request, 1)
    fresh.observation.issued_at = clock[0]
    fresh.observation.expires_at = clock[0] + 10
    await store.voiceprints.record_candidate_evidence('owner', fresh)
    clock[0] += 11
    assert len((await store.voiceprints.recognition_candidates('owner')).persons) == 1
    clock[0] += 50
    assert not (await store.voiceprints.recognition_candidates('owner')).persons


async def test_candidate_without_receipts_expires_but_established_person_does_not(store, monkeypatch):
    import time
    from eidolon_sdk.biz.person import PersonCreate
    clock = [time.time()]
    monkeypatch.setattr('eidolon_data.services.voiceprints.time.time', lambda: clock[0])
    store.settings.candidate_pool_ttl_s = 60
    await store.owner_commands.create_owner(owner_id='owner')
    for char, maturity in [('a', 'candidate'), ('b', 'established')]:
        await store.persons.create('owner', PersonCreate(person_id=char * 32, display_name=char, maturity=maturity))
    clock[0] += 61
    await store.voiceprints.recognition_candidates('owner')
    assert (await store.persons.get('owner', 'a' * 32)).recognition_state == 'retained'
    assert (await store.persons.get('owner', 'b' * 32)).recognition_state == 'active'


async def test_observation_profile_has_real_provenance_and_source_removal_revokes_template(store):
    from sqlalchemy import select
    from eidolon_data.schema.voiceprints import CandidateVoiceObservationRow, SpeakerProfileRow
    request = await candidate_evidence_setup(store)
    await store.voiceprints.record_candidate_evidence('owner', request)
    async with store._session_factory() as session:
        receipt = await session.scalar(select(CandidateVoiceObservationRow))
        profile = await session.get(SpeakerProfileRow, chosen(request).profile_id)
        profile.origin = 'observation'
        profile.enrollment_id = None
        profile.source_observation_id = receipt.observation_id
        await session.commit()
    profile_view = (await store.voiceprints.view('owner')).profiles[0]
    assert profile_view.origin == 'observation' and profile_view.enrollment_id is None
    assert profile_view.source_observation_id == receipt.observation_id
    persons = (await store.voiceprints.recognition_candidates('owner')).persons
    # An observation template is from the channel of the probe that created it.
    assert len(persons) == 1 and persons[0].templates[0].channel == 'esp-box-3'
    async with store._session_factory() as session:
        source = await session.get(CandidateVoiceObservationRow, receipt.observation_id)
        data = dict(source.observation_json)
        data['source'] = {k: v for k, v in data['source'].items() if k != 'channel'}
        source.observation_json = data
        await session.commit()
    # A receipt that never recorded its channel is not passed off as the phone's.
    persons = (await store.voiceprints.recognition_candidates('owner')).persons
    assert persons[0].templates[0].channel == 'unknown'
    async with store._session_factory() as session:
        source = await session.get(CandidateVoiceObservationRow, receipt.observation_id)
        await session.delete(source)
        await session.commit()
    assert not (await store.voiceprints.recognition_candidates('owner')).persons
    assert not (await store.voiceprints.view('owner')).profiles
    with pytest.raises(LookupError):
        await store.voiceprints.recording('owner', chosen(request).profile_id)
    person_id = chosen(request).person_id
    assert (await store.persons.get('owner', person_id)).person_id == person_id


async def test_recognition_snapshot_includes_all_bounded_people_and_never_truncates(store):
    from eidolon_sdk.biz.person import PersonCreate
    from eidolon_sdk.biz.speaker_identity import MAX_RECOGNITION_PERSONS
    from eidolon_data.schema.voiceprints import SpeakerProfileRow
    await store.owner_commands.create_owner(owner_id='owner')
    for n in range(MAX_RECOGNITION_PERSONS + 1):
        person_id = f'{n:032x}'
        await store.persons.create('owner', PersonCreate(person_id=person_id, display_name=f'Person {n}'))
        async with store._session_factory() as session:
            session.add(SpeakerProfileRow(profile_id=f'v{n}', owner_id='owner', person_id=person_id,
                revision=1, enrollment_id=f'e{n}', template_json=sample().model_dump()))
            await session.commit()
        if n == MAX_RECOGNITION_PERSONS - 1:
            snapshot = await store.voiceprints.recognition_candidates('owner')
            assert len(snapshot.persons) == MAX_RECOGNITION_PERSONS
            assert len({p.person_id for p in snapshot.persons}) == MAX_RECOGNITION_PERSONS
    with pytest.raises(VoiceprintConflict, match='capacity'):
        await store.voiceprints.recognition_candidates('owner')


async def test_promotion_does_not_reclassify_legacy_missing_overlap_as_clean(store):
    from sqlalchemy import select
    from eidolon_data.schema.voiceprints import CandidateVoiceObservationRow

    store.settings.candidate_promotion = promotion_policy()
    request = await candidate_evidence_setup(store)
    await store.voiceprints.record_candidate_evidence('owner', request)
    # Simulate a retained receipt written before the new quality contract.
    async with store._session_factory() as session:
        row = await session.scalar(select(CandidateVoiceObservationRow))
        data = dict(row.observation_json)
        data['audio_quality'] = dict(data['audio_quality'])
        data['audio_quality'].pop('overlap_detected')
        row.observation_json = data
        await session.commit()
    for n in (1, 2):
        result = await store.voiceprints.record_candidate_evidence(
            'owner', another_candidate_turn(request, n))
        assert result.maturity == 'candidate'
    result = await store.voiceprints.record_candidate_evidence(
        'owner', another_candidate_turn(request, 3))
    assert result.maturity == 'established'
    async with store._session_factory() as session:
        rows = (await session.scalars(select(CandidateVoiceObservationRow))).all()
        assert len(rows) == 4  # Old evidence retained, never retroactively certified.
        assert sum('overlap_detected' not in row.observation_json['audio_quality'] for row in rows) == 1


async def test_promotion_never_counts_a_receipt_in_an_older_observation_shape(store):
    from sqlalchemy import select

    from eidolon_data.schema.voiceprints import CandidateVoiceObservationRow

    store.settings.candidate_promotion = promotion_policy()
    request = await candidate_evidence_setup(store)
    await store.voiceprints.record_candidate_evidence('owner', request)
    # A retained v1 receipt: a hard verdict and a raw cosine, no calibrated evidence.
    async with store._session_factory() as session:
        row = await session.scalar(select(CandidateVoiceObservationRow))
        data = {k: v for k, v in row.observation_json.items() if k not in (
            'evidence', 'posterior_unknown', 'decisions', 'backend_version', 'policy_version')}
        data.update(schema_version=1, status='identified', person_id='a' * 32, person_revision=1,
            profile_id=row.profile_id, profile_revision=row.profile_revision, maturity='candidate',
            is_named=False, confidence='controlled_trial', cosine_similarity=0.99,
            scoring_version='normalized-template-cosine-v1', decision_version=IDENTITY_POLICY)
        row.observation_json = data
        await session.commit()
    for n in (1, 2):
        result = await store.voiceprints.record_candidate_evidence('owner', another_candidate_turn(request, n))
        assert result.maturity == 'candidate' and result.independent_turns == n
    result = await store.voiceprints.record_candidate_evidence('owner', another_candidate_turn(request, 3))
    assert result.maturity == 'established' and result.independent_turns == 3


def windows(similarity):
    from eidolon_sdk.biz.voiceprint import CalibrationCrop, GenuineScore, TemplateCalibration
    return TemplateCalibration(
        crops=[CalibrationCrop(speech_ms=900, vector=[1.0, 0.0])],
        genuine=[GenuineScore(speech_ms=900, similarity=similarity)])


async def test_recognition_carries_the_windows_of_the_registration_that_made_the_template(store):
    from eidolon_sdk.biz.voiceprint import CalibrationCrop, EnrollmentSample
    await setup(store)
    v = store.voiceprints

    async def register(op, similarity):
        e = await v.create_enrollment(
            "owner", EnrollmentCreate(person_id="a" * 32, operation_id=op))
        window = CalibrationCrop(speech_ms=900, vector=[1.0, 0.0])
        for i in range(3):
            await v.add_sample("owner", e.enrollment_id, str(i),
                               EnrollmentSample(**sample(i).model_dump(), crops=[window]))
        assert (await v.evidence("owner", e.enrollment_id)).samples["0"].crops == [window]
        return await complete(v, "owner", e.enrollment_id, sample(3), calibration=windows(similarity))

    first = await register("first", 0.71)
    template = (await v.recognition_candidates("owner")).persons[0].templates[0]
    assert template.calibration == windows(0.71)
    second = await register("second", 0.82)
    assert second.revision == first.revision + 1
    template = (await v.recognition_candidates("owner")).persons[0].templates[0]
    # Only the registration behind this exact template revision counts.
    assert template.revision == second.revision and template.calibration == windows(0.82)


async def test_a_registration_without_windows_has_no_calibration(store):
    await setup(store)
    await enroll(store)
    template = (await store.voiceprints.recognition_candidates("owner")).persons[0].templates[0]
    assert template.calibration is None
