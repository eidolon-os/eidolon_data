import asyncio
import time

import pytest
from eidolon_sdk.biz.candidate_evidence import CandidateDiscovery, CandidateDiscoveryPolicy

from eidolon_data.services.voiceprints import VoiceprintConflict

IDENTITY_POLICY = 'identity-trial'


async def setup(store, *, channel='mobile'):
    await store.owner_commands.create_owner(owner_id='owner')
    store.settings.candidate_discovery = CandidateDiscoveryPolicy(
        version='discovery-trial', identity_policy_version=IDENTITY_POLICY)
    snapshot = await store.voiceprints.recognition_candidates('owner')
    return discovery(snapshot, channel=channel)


def discovery(snapshot, *, channel='mobile', evidence=()):
    """A signed observation deciding the voice is likely someone not enrolled."""
    now = time.time()
    return CandidateDiscovery(policy_version='discovery-trial', snapshot_revision=snapshot.revision,
        audio_sha256='c' * 64, sample=dict(model_id='m', weights_hash='a' * 64,
            preprocessing_version='v', vector=[1, 0], speech_ms=2000, quality={'overlap_detected': 0}),
        observation=dict(status='compared', owner_id='owner', session_id='s', turn_id='t',
            source=dict(request_id='t', session_id='s', input_id='mic', generation=1,
                sample_rate=16000, start_sample=0, end_sample=32000, channel=channel),
            issued_at=now, expires_at=now+10, reason='compared',
            model_id='m', weights_hash='a' * 64, preprocessing_version='v',
            backend_version='identity-backend-v1', policy_version=IDENTITY_POLICY,
            evidence=list(evidence), posterior_unknown=0.995, decisions=dict(discovery=True),
            audio_quality=dict(audio_ms=2000, speech_ms=2000, required_speech_ms=1500, overlap_detected=0)))


def another_turn(request, snapshot_revision):
    other = request.model_copy(deep=True)
    other.audio_sha256 = 'd' * 64
    other.observation.turn_id = other.observation.source.request_id = 'other'
    other.observation.source.start_sample = 32000
    other.observation.source.end_sample = 64000
    other.snapshot_revision = snapshot_revision
    return other


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
    other = another_turn(request, request.snapshot_revision)
    results = await asyncio.gather(store.voiceprints.discover_candidate('owner', request),
        store.voiceprints.discover_candidate('owner', other), return_exceptions=True)
    assert sum(isinstance(result, VoiceprintConflict) for result in results) == 1
    assert len(await store.persons.list('owner')) == 1


@pytest.mark.parametrize('change', ['disabled', 'owner', 'expired', 'policy', 'identity_policy'])
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
    if change == 'identity_policy':
        # Decided under an identity policy this discovery policy does not name.
        request.observation.policy_version = 'other'
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


async def test_discovery_requires_the_signed_decision_under_the_named_identity_policy(store):
    from pydantic import ValidationError
    request = await setup(store)
    first = await store.voiceprints.discover_candidate('owner', request)
    snapshot = await store.voiceprints.recognition_candidates('owner')
    template = snapshot.persons[0].templates[0]
    # Compared against the current, complete snapshot: the existing candidate is unlikely.
    known = dict(person_id=first.person_id, person_revision=first.revision, maturity='candidate',
        is_named=False, profile_id=template.profile_id, profile_revision=template.revision,
        llr=-8.0, posterior=0.004)
    other = another_turn(discovery(snapshot, evidence=[known]), snapshot.revision)
    stale = other.model_copy(deep=True)
    stale.observation.policy_version = 'older-identity'
    with pytest.raises(VoiceprintConflict, match='mismatch'):
        await store.voiceprints.discover_candidate('owner', stale)
    # Without the discovery decision the request cannot even be formed.
    undecided = other.model_dump(mode='json')
    undecided['observation']['decisions'] = {}
    with pytest.raises(ValidationError):
        CandidateDiscovery.model_validate(undecided)
    assert len(await store.persons.list('owner')) == 1
    # The signed decision alone excludes existing identities; no score is re-judged here.
    second = await store.voiceprints.discover_candidate('owner', other)
    assert second.person_id != first.person_id
    assert len(await store.persons.list('owner')) == 2


async def test_discovery_policy_change_invalidates_snapshot(store):
    request = await setup(store)
    store.settings.candidate_discovery = CandidateDiscoveryPolicy(
        version='discovery-trial-2', identity_policy_version=IDENTITY_POLICY)
    request.policy_version = 'discovery-trial-2'
    with pytest.raises(VoiceprintConflict, match='snapshot'):
        await store.voiceprints.discover_candidate('owner', request)
    assert await store.persons.list('owner') == []


async def test_templates_carry_the_channel_they_were_captured_on(store):
    from eidolon_sdk.biz.person import PersonCreate
    from test_voiceprints import enroll
    request = await setup(store, channel='esp-box-3')
    discovered = await store.voiceprints.discover_candidate('owner', request)
    await store.persons.create('owner', PersonCreate(person_id='a' * 32, display_name='甲'))
    await enroll(store)
    snapshot = await store.voiceprints.recognition_candidates('owner')
    channels = {person.person_id: person.templates[0].channel for person in snapshot.persons}
    # Registration is captured on the phone; an observation keeps its probe's channel.
    assert channels == {discovered.person_id: 'esp-box-3', 'a' * 32: 'mobile'}
    templates = await store.voiceprints.templates('owner', discovered.person_id)
    assert [t.channel for t in templates.templates] == ['esp-box-3']
    templates = await store.voiceprints.templates('owner', 'a' * 32)
    assert [t.channel for t in templates.templates] == ['mobile']


@pytest.mark.parametrize("automatic,suspended", [(False, False), (True, False), (True, True), ("revoked", False), (True, "legacy")])
async def test_discovered_candidate_can_accumulate_verified_turns_and_promote_without_rebinding(store, automatic, suspended):
    from eidolon_sdk.biz.candidate_evidence import CandidateEvidence, CandidatePromotionPolicy
    from eidolon_sdk.biz.speaker_identity import SpeakerIdentityObservation
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
    store.settings.candidate_promotion = CandidatePromotionPolicy(version='promotion',
        identity_policy_version=IDENTITY_POLICY, model_id='m', weights_hash='a' * 64,
        preprocessing_version='v', min_posterior=.95)
    for n in range(1, 4):
        if n == 3 and automatic == "revoked":
            defaults = await store.persons.memory_defaults('owner')
            await store.persons.memory_defaults('owner', defaults['owner_revision'], PersonMemoryDefaults())
        snapshot = await store.voiceprints.recognition_candidates('owner')
        candidate = snapshot.persons[0]
        template = candidate.templates[0]
        value = request.observation.model_dump(mode='json')
        value.update(turn_id=f'verified-{n}', posterior_unknown=.02,
            decisions=dict(candidate_evidence=person.person_id),
            evidence=[dict(person_id=person.person_id, person_revision=candidate.person_revision,
                maturity='candidate', is_named=False, profile_id=template.profile_id,
                profile_revision=template.revision, llr=6.0, posterior=.98)])
        value['source'].update(request_id=f'verified-{n}', start_sample=n * 32000, end_sample=(n + 1) * 32000)
        evidence = CandidateEvidence(snapshot_revision=snapshot.revision, audio_sha256=f'{n:064x}',
            observation=SpeakerIdentityObservation.model_validate(value))
        result = await store.voiceprints.record_candidate_evidence('owner', evidence)
        assert result.maturity == ('established' if n == 3 else 'candidate')
    promoted = await store.persons.get('owner', person.person_id)
    assert promoted.person_id == person.person_id and promoted.revision == person.revision + 1
    after = await store.persons.memory_state('owner', person.person_id)
    assert after['realm_id'] == realm['realm_id']
    assert after['state'] == ('active' if automatic is True and not suspended else 'inactive')
    assert (await store.voiceprints.view('owner')).profiles[0].origin == 'observation'
