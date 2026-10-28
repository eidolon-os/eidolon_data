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


async def candidate_evidence_setup(store):
    from eidolon_sdk.biz.person import PersonCreate
    from eidolon_sdk.biz.candidate_evidence import CandidateEvidence
    from eidolon_sdk.biz.speaker_identity import SpeakerIdentityObservation, SpeakerEvidenceSource
    from eidolon_sdk.biz.voiceprint import SpeakerAudioQuality
    import time
    await store.owner_commands.create_owner(owner_id='owner')
    await store.persons.create('owner', PersonCreate(person_id='a' * 32,
        display_name='匿名', maturity='candidate', is_named=False))
    profile = await enroll(store)
    snapshot = await store.voiceprints.recognition_candidates('owner')
    person = snapshot.persons[0]
    now = time.time()
    observation = SpeakerIdentityObservation(status='identified', owner_id='owner',
        source=SpeakerEvidenceSource(request_id='t', session_id='s', input_id='mic',
            generation=1, sample_rate=16000, start_sample=0, end_sample=32000),
        session_id='s', turn_id='t', issued_at=now, expires_at=now + 10,
        person_id=person.person_id, person_revision=person.person_revision,
        profile_id=profile.profile_id, profile_revision=profile.revision,
        model_id='test', weights_hash='a' * 64, preprocessing_version='v1',
        scoring_version='normalized-template-cosine-v1', decision_version='trial',
        maturity='candidate', is_named=False, confidence='controlled_trial', reason='voice_match',
        audio_quality=SpeakerAudioQuality(audio_ms=2000, speech_ms=2000, required_speech_ms=1500))
    return CandidateEvidence(snapshot_revision=snapshot.revision, audio_sha256='c' * 64, observation=observation)


def another_candidate_turn(request, n, *, overlap=False):
    from eidolon_sdk.biz.candidate_evidence import CandidateEvidence
    payload = request.model_dump()
    payload['audio_sha256'] = f'{n:064x}'
    value = payload['observation']
    value['turn_id'] = value['source']['request_id'] = f't{n}'
    value['source']['start_sample'] = 1 if overlap else n * 32000
    value['source']['end_sample'] = 32001 if overlap else (n + 1) * 32000
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
    assert person.maturity == 'candidate' and person.revision == request.observation.person_revision
    assert (await store.persons.memory_state('owner', person.person_id))['state'] == 'inactive'


async def test_candidate_receipts_reject_replay_overlap_stale_scope_and_retirement(store):
    request = await candidate_evidence_setup(store)
    await store.voiceprints.record_candidate_evidence('owner', request)
    for changed, reason in [
        (another_candidate_turn(request, 1, overlap=True), 'overlapping'),
        (another_candidate_turn(request, 1).model_copy(update={'audio_sha256': request.audio_sha256}), 'already counted'),
        (another_candidate_turn(request, 1).model_copy(update={'snapshot_revision': 'f' * 64}), 'snapshot'),
    ]:
        with pytest.raises(VoiceprintConflict, match=reason):
            await store.voiceprints.record_candidate_evidence('owner', changed)
    with pytest.raises(VoiceprintConflict, match='owner'):
        await store.voiceprints.record_candidate_evidence('other', request)
    await store.persons.retire_candidate('owner', 'a' * 32, request.observation.person_revision)
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
    return CandidatePromotionPolicy(**(dict(version='promotion-trial-v1', decision_version='trial',
        model_id='test', weights_hash='a' * 64, preprocessing_version='v1', min_similarity=0.8) | changes))


async def test_configured_promotion_is_atomic_stable_and_does_not_grant_memory(store):
    store.settings.candidate_promotion = promotion_policy()
    request = await candidate_evidence_setup(store)
    request.observation.cosine_similarity = 0.9
    original = await store.persons.memory_state('owner', 'a' * 32)
    for n in range(3):
        current = request if n == 0 else another_candidate_turn(request, n)
        result = await store.voiceprints.record_candidate_evidence('owner', current)
        assert result.maturity == ('candidate' if n < 2 else 'established')
    person = await store.persons.get('owner', 'a' * 32)
    assert person.person_id == request.observation.person_id and person.is_named is False
    assert person.revision == request.observation.person_revision + 1
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


@pytest.mark.parametrize('policy_change,similarity', [
    ({'decision_version': 'other'}, 0.9), ({'model_id': 'other'}, 0.9),
    ({}, 0.7), ({'min_total_speech_ms': 9000}, 0.9),
])
async def test_promotion_requires_matching_policy_and_sufficient_evidence(store, policy_change, similarity):
    store.settings.candidate_promotion = promotion_policy(**policy_change)
    request = await candidate_evidence_setup(store)
    request.observation.cosine_similarity = similarity
    for n in range(3):
        await store.voiceprints.record_candidate_evidence('owner', request if n == 0 else another_candidate_turn(request, n))
    assert (await store.persons.get('owner', 'a' * 32)).maturity == 'candidate'


async def test_promotion_does_not_mix_decision_versions(store):
    store.settings.candidate_promotion = promotion_policy()
    request = await candidate_evidence_setup(store)
    request.observation.cosine_similarity = 0.9
    older = request.model_copy(deep=True)
    older.observation.decision_version = 'older-trial'
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
    recording = await store.voiceprints.recording('owner', request.observation.profile_id)
    realm = await store.persons.memory_state('owner', 'a' * 32)
    clock[0] += 50
    assert not (await store.voiceprints.record_candidate_evidence('owner', request)).accepted
    clock[0] += 11
    assert not (await store.voiceprints.recognition_candidates('owner')).persons
    person = await store.persons.get('owner', 'a' * 32)
    assert person.recognition_state == 'retained' and person.status == 'active'
    assert person.revision == request.observation.person_revision + 1
    assert await store.voiceprints.view('owner') == profiles
    assert await store.voiceprints.recording('owner', request.observation.profile_id) == recording
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
        profile = await session.get(SpeakerProfileRow, request.observation.profile_id)
        profile.origin = 'observation'
        profile.enrollment_id = None
        profile.source_observation_id = receipt.observation_id
        await session.commit()
    profile_view = (await store.voiceprints.view('owner')).profiles[0]
    assert profile_view.origin == 'observation' and profile_view.enrollment_id is None
    assert profile_view.source_observation_id == receipt.observation_id
    assert len((await store.voiceprints.recognition_candidates('owner')).persons) == 1
    async with store._session_factory() as session:
        source = await session.get(CandidateVoiceObservationRow, receipt.observation_id)
        await session.delete(source)
        await session.commit()
    assert not (await store.voiceprints.recognition_candidates('owner')).persons
    assert not (await store.voiceprints.view('owner')).profiles
    with pytest.raises(LookupError):
        await store.voiceprints.recording('owner', request.observation.profile_id)
    assert (await store.persons.get('owner', request.observation.person_id)).person_id == request.observation.person_id
