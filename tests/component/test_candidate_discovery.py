import asyncio
import time

import pytest
from eidolon_sdk.biz.candidate_evidence import CandidateDiscovery, CandidateDiscoveryPolicy

from eidolon_data.services.voiceprints import VoiceprintConflict


async def setup(store):
    await store.owner_commands.create_owner(owner_id='owner')
    store.settings.candidate_discovery = CandidateDiscoveryPolicy(version='discovery-trial',
        recognition=dict(version='trial', model_id='m', weights_hash='a' * 64,
            preprocessing_version='v', threshold=.6, min_person_margin=.1, min_speech_ms=1500),
        max_existing_similarity=.2)
    snapshot = await store.voiceprints.recognition_candidates('owner')
    now = time.time()
    return CandidateDiscovery(policy_version='discovery-trial', snapshot_revision=snapshot.revision,
        audio_sha256='c' * 64, sample=dict(model_id='m', weights_hash='a' * 64,
            preprocessing_version='v', vector=[1, 0], speech_ms=2000, quality={'overlap_detected': 0}),
        observation=dict(status='unknown', owner_id='owner', session_id='s', turn_id='t',
            source=dict(request_id='t', session_id='s', input_id='mic', generation=1,
                sample_rate=16000, start_sample=0, end_sample=32000),
            issued_at=now, expires_at=now+10, confidence='controlled_trial', reason='no_match',
            model_id='m', weights_hash='a' * 64, preprocessing_version='v', scoring_version='normalized-template-cosine-v1',
            decision_version='trial', audio_quality=dict(audio_ms=2000, speech_ms=2000, required_speech_ms=1500, overlap_detected=0)))


async def test_discovery_atomically_creates_one_candidate_template_source_and_inactive_realm(store):
    request = await setup(store)
    a, b = await asyncio.gather(*(store.voiceprints.discover_candidate('owner', request) for _ in range(2)))
    assert a == b and a.maturity == 'candidate' and not a.is_named
    assert len(await store.persons.list('owner')) == 1
    profile = (await store.voiceprints.view('owner')).profiles[0]
    assert profile.origin == 'observation' and profile.enrollment_id is None
    assert profile.source_observation_id and not profile.recording_available
    state = await store.persons.memory_state('owner', a.person_id)
    assert state['realm_id'] and state['state'] == 'inactive'
    snapshot = await store.voiceprints.recognition_candidates('owner')
    assert snapshot.persons[0].person_id == a.person_id
    assert snapshot.revision != request.snapshot_revision
    await store.persons.retire_candidate('owner', a.person_id, a.revision)
    with pytest.raises(VoiceprintConflict, match='revoked'):
        await store.voiceprints.discover_candidate('owner', request)


async def test_stale_parallel_discovery_cannot_create_a_second_person(store):
    request = await setup(store)
    other = request.model_copy(deep=True)
    other.audio_sha256 = 'd' * 64
    other.observation.turn_id = other.observation.source.request_id = 'other'
    other.observation.source.start_sample = 32000
    other.observation.source.end_sample = 64000
    results = await asyncio.gather(store.voiceprints.discover_candidate('owner', request),
        store.voiceprints.discover_candidate('owner', other), return_exceptions=True)
    assert sum(isinstance(result, VoiceprintConflict) for result in results) == 1
    assert len(await store.persons.list('owner')) == 1


@pytest.mark.parametrize('change', ['disabled', 'owner', 'expired', 'policy'])
async def test_invalid_discovery_leaves_no_partial_person_or_realm(store, change):
    request = await setup(store)
    owner = 'owner'
    if change == 'disabled':
        store.settings.candidate_discovery = None
    if change == 'owner':
        owner = 'other'
    if change == 'expired':
        request.observation.expires_at = time.time() - 1
    if change == 'policy':
        request.policy_version = 'other'
    with pytest.raises(VoiceprintConflict):
        await store.voiceprints.discover_candidate(owner, request)
    assert await store.persons.list('owner') == []
    assert await store.memory_realms.list_for_owner('owner') == []


async def test_discovery_http_requires_service_auth(store):
    import httpx
    from fastapi import FastAPI

    from eidolon_data.api.routers.voiceprints import create_voiceprint_router
    request = await setup(store)
    app = FastAPI()
    app.include_router(create_voiceprint_router(store, 'secret'))
    path = '/api/companion-authority/v1/owners/owner/voiceprints/candidate-discovery'
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
        assert (await client.post(path, json=request.model_dump(mode='json'))).status_code == 401
        response = await client.post(path, json=request.model_dump(mode='json'), headers={'Authorization': 'Bearer secret'})
        assert response.status_code == 200 and response.json()['maturity'] == 'candidate'


async def test_discovery_requires_exclusion_margin_against_current_complete_snapshot(store):
    request = await setup(store)
    await store.voiceprints.discover_candidate('owner', request)
    other = request.model_copy(deep=True)
    other.audio_sha256 = 'd' * 64
    other.observation.turn_id = other.observation.source.request_id = 'other'
    other.observation.source.start_sample = 32000
    other.observation.source.end_sample = 64000
    other.snapshot_revision = (await store.voiceprints.recognition_candidates('owner')).revision
    for similarity in (None, .21, .9):
        other.observation.cosine_similarity = similarity
        with pytest.raises(VoiceprintConflict, match='exclusion'):
            await store.voiceprints.discover_candidate('owner', other)
    assert len(await store.persons.list('owner')) == 1


async def test_discovery_policy_change_invalidates_snapshot(store):
    request = await setup(store)
    store.settings.candidate_discovery.max_existing_similarity = .1
    with pytest.raises(VoiceprintConflict, match='snapshot'):
        await store.voiceprints.discover_candidate('owner', request)
    assert await store.persons.list('owner') == []


@pytest.mark.parametrize("automatic,suspended", [(False, False), (True, False), (True, True), ("revoked", False), (True, "legacy")])
async def test_discovered_candidate_can_accumulate_verified_turns_and_promote_without_rebinding(store, automatic, suspended):
    from eidolon_sdk.biz.candidate_evidence import CandidateEvidence, CandidatePromotionPolicy
    request = await setup(store)
    from eidolon_sdk.biz.system_data.models import PersonMemoryDefaults
    defaults = await store.persons.memory_defaults('owner')
    await store.persons.memory_defaults('owner', defaults['owner_revision'],
                                       PersonMemoryDefaults(prepare_on_promotion=bool(automatic), candidate={"save_allowed": True}))
    person = await store.voiceprints.discover_candidate('owner', request)
    if suspended is True:
        await store.persons.suspend_memory('owner', person.person_id, person.revision)
        person = await store.persons.get('owner', person.person_id)
    if suspended == "legacy":
        from sqlalchemy import select

        from eidolon_data.schema.core import MemoryRealmRow
        async with store.persons._transaction() as session:
            row = await session.scalar(select(MemoryRealmRow).where(MemoryRealmRow.person_id == person.person_id))
            row.policy_json = {"scope": "person", "provisional_memory": {"save_allowed": False}}
    provisional = await store.persons.provisional_memory_policy('owner', person.person_id)
    assert provisional['policy']['save_allowed'] == (not suspended)
    realm = await store.persons.memory_state('owner', person.person_id)
    store.settings.candidate_promotion = CandidatePromotionPolicy(version='promotion', decision_version='trial',
        model_id='m', weights_hash='a' * 64, preprocessing_version='v', min_similarity=.8)
    for n in range(1, 4):
        if n == 3 and automatic == "revoked":
            defaults = await store.persons.memory_defaults('owner')
            await store.persons.memory_defaults('owner', defaults['owner_revision'], PersonMemoryDefaults())
        snapshot = await store.voiceprints.recognition_candidates('owner')
        candidate = snapshot.persons[0]
        template = candidate.templates[0]
        value = request.observation.model_copy(deep=True)
        value.status = 'identified'
        value.person_id = person.person_id
        value.person_revision = candidate.person_revision
        value.profile_id = template.profile_id
        value.profile_revision = template.revision
        value.maturity = 'candidate'
        value.cosine_similarity = .9
        value.turn_id = value.source.request_id = f'verified-{n}'
        value.source.start_sample = n * 32000
        value.source.end_sample = (n + 1) * 32000
        evidence = CandidateEvidence(snapshot_revision=snapshot.revision, audio_sha256=f'{n:064x}', observation=value)
        result = await store.voiceprints.record_candidate_evidence('owner', evidence)
        assert result.maturity == ('established' if n == 3 else 'candidate')
    promoted = await store.persons.get('owner', person.person_id)
    assert promoted.person_id == person.person_id and promoted.revision == person.revision + 1
    after = await store.persons.memory_state('owner', person.person_id)
    assert after['realm_id'] == realm['realm_id']
    assert after['state'] == ('active' if automatic is True and not suspended else 'inactive')
    assert (await store.voiceprints.view('owner')).profiles[0].origin == 'observation'
