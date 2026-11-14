"""Voice attribution and per-channel observation templates (design §3.2, §3.5; A1)."""

import time

import pytest
from eidolon_sdk.biz.speaker_identity import SpeakerEvidenceSource, VoiceRef
from eidolon_sdk.biz.voice_attribution import VerdictEvidence, VoiceVerdict
from eidolon_sdk.biz.voiceprint import EnrollmentCreate, ExtractedSample

from eidolon_data.services.voiceprints import VoiceprintConflict

BOX = "esp-box-3"
MODEL = dict(model_id="test", weights_hash="a" * 64, preprocessing_version="v1")
PERSON = "a" * 32


def sample(n=0, **change):
    return ExtractedSample(**(MODEL | dict(vector=[1.0, n * 0.01], speech_ms=4500,
                                           quality={"clipping": 0.0}) | change))


async def enroll(store):
    import base64
    import hashlib

    from eidolon_sdk.biz.person import PersonCreate
    from eidolon_sdk.biz.voiceprint import (
        EnrollmentCompletion,
        EnrollmentEvaluation,
        VoiceAudioRequest,
        enrollment_evidence_digest,
    )

    await store.owner_commands.create_owner(owner_id="owner")
    await store.persons.create("owner", PersonCreate(person_id=PERSON, display_name="甲"))
    v = store.voiceprints
    e = await v.create_enrollment("owner", EnrollmentCreate(person_id=PERSON, operation_id="enroll"))
    for i in range(3):
        await v.add_sample("owner", e.enrollment_id, str(i), sample(i))
    recording = VoiceAudioRequest(pcm_base64=base64.b64encode(b"\x01\x00" * 32000).decode())
    samples = (await v.evidence("owner", e.enrollment_id)).samples
    await v.complete("owner", e.enrollment_id, EnrollmentCompletion(
        evaluation=EnrollmentEvaluation(
            evidence_digest=enrollment_evidence_digest(samples),
            recording_sha256=hashlib.sha256(base64.b64decode(recording.pcm_base64)).hexdigest(),
            template=next(iter(samples.values())), trial=sample(3)),
        recording=recording))


def ref(turn="t1", session="s1", track="tr1", channel=BOX):
    return VoiceRef(owner_id="owner", session_id=session, turn_id=turn, track_id=track, speech_ms=9000,
                    source=SpeakerEvidenceSource(request_id=turn, session_id=session, input_id="mic",
                                                 generation=1, sample_rate=16000, start_sample=0,
                                                 end_sample=48000, channel=channel))


async def verdict(store, *, turn="t1", session="s1", track="tr1", channel=BOX, template=None, **change):
    snapshot = await store.voiceprints.recognition_candidates("owner", channel, session)
    person = next(p for p in snapshot.persons if p.person_id == PERSON)
    now = time.time()
    return VoiceVerdict(**({
        "kind": "turn", "owner_id": "owner", "refs": [ref(turn, session, track, channel)],
        "person_id": PERSON, "person_revision": person.person_revision,
        "evidence": VerdictEvidence(llr=8.0, posterior=0.999, posterior_unknown=0.0005,
                                    condition="far_cross"),
        "sample": template, "sample_channel": channel if template is not None else None,
        **MODEL, "backend_version": "identity-backend-v4", "policy_version": "identity-policy-v6",
        "snapshot_revision": snapshot.revision, "issued_at": now, "expires_at": now + 10,
    } | change))


def track_sample(n=1):
    return ExtractedSample.track_mean([[1.0, 0.0], [n * 0.1, 1.0]], 9000, **MODEL)


def channel_templates(snapshot):
    return [t for p in snapshot.persons for t in p.templates if t.channel == BOX]


async def test_a_turn_verdict_records_one_attribution_and_replays_idempotently(store):
    await enroll(store)
    request = await verdict(store)
    first = await store.voice_attributions.apply_verdict("owner", request)
    assert (first.person_id, first.source, first.revoked, first.revision) == (PERSON, "turn", False, 1)
    assert [r.key() for r in first.refs] == [("owner", "s1", "t1")]
    assert await store.voice_attributions.apply_verdict("owner", request) == first
    other = request.model_copy(update={"evidence": VerdictEvidence(
        llr=9.0, posterior=0.9995, posterior_unknown=0.0001, condition="far_cross")})
    with pytest.raises(VoiceprintConflict, match="already attributed"):
        await store.voice_attributions.apply_verdict("owner", other)
    feed = await store.voice_attributions.feed("owner")
    assert [c.attribution for c in feed.changes] == [first] and feed.next_after == 1


@pytest.mark.parametrize("change,reason", [
    ({"kind": "attribute", "refs": [ref("t1"), ref("t2")]}, "kind not accepted"),
    ({"snapshot_revision": "f" * 64}, "snapshot changed"),
    ({"person_revision": 99}, "Person changed"),
    ({"model_id": "other"}, "model does not match"),
    ({"issued_at": 1.0, "expires_at": 5.0}, "expired"),
])
async def test_a_verdict_is_refused_unless_it_holds_now(store, change, reason):
    await enroll(store)
    with pytest.raises(VoiceprintConflict, match=reason):
        await store.voice_attributions.apply_verdict("owner", await verdict(store, **change))
    assert (await store.voice_attributions.feed("owner")).changes == []


async def test_a_verdict_belongs_to_its_owner_and_a_template_to_a_known_channel(store):
    await enroll(store)
    await store.owner_commands.create_owner(owner_id="other")
    with pytest.raises(VoiceprintConflict, match="owner mismatch"):
        await store.voice_attributions.apply_verdict("other", await verdict(store))
    unknown = await verdict(store, channel="unknown", template=track_sample())
    with pytest.raises(VoiceprintConflict, match="known channel"):
        await store.voice_attributions.apply_verdict("owner", unknown)
    untracked = await verdict(store, track=None)
    untracked = untracked.model_copy(update={"sample": track_sample(), "sample_channel": BOX})
    with pytest.raises(VoiceprintConflict, match="track"):
        await store.voice_attributions.apply_verdict("owner", untracked)


async def test_templates_serve_their_channel_but_never_their_own_conversation(store):
    await enroll(store)
    await store.voice_attributions.apply_verdict("owner", await verdict(store, template=track_sample()))
    assert channel_templates(await store.voiceprints.recognition_candidates("owner", BOX, "s1")) == []
    assert channel_templates(await store.voiceprints.recognition_candidates("owner", "stackchan", "s2")) == []
    assert channel_templates(await store.voiceprints.recognition_candidates("owner")) == []
    [served] = channel_templates(await store.voiceprints.recognition_candidates("owner", BOX, "s2"))
    assert served.revision == 1 and served.sample.mean_length == pytest.approx(track_sample().mean_length)
    assert served.sample.scored_vector() == pytest.approx(track_sample().scored_vector())


async def test_a_track_keeps_one_template_and_a_channel_its_latest_three(store):
    await enroll(store)
    apply = store.voice_attributions.apply_verdict
    await apply("owner", await verdict(store, turn="t1", template=track_sample(1)))
    await apply("owner", await verdict(store, turn="t2", template=track_sample(2)))
    [same] = channel_templates(await store.voiceprints.recognition_candidates("owner", BOX, "elsewhere"))
    assert same.revision == 2 and same.sample.vector == pytest.approx(track_sample(2).vector)
    for n, session in enumerate(("s2", "s3", "s4"), 3):
        await apply("owner", await verdict(store, turn=f"t{n}", session=session, template=track_sample(n)))
    kept = channel_templates(await store.voiceprints.recognition_candidates("owner", BOX, "elsewhere"))
    assert [t.sample.vector for t in kept] == [pytest.approx(track_sample(n).vector) for n in (5, 4, 3)]


async def test_archiving_the_person_revokes_attributions_in_feed_order_and_withdraws_templates(store):
    await enroll(store)
    apply = store.voice_attributions.apply_verdict
    a = await apply("owner", await verdict(store, turn="t1", template=track_sample()))
    b = await apply("owner", await verdict(store, turn="t2", session="s2"))
    person = await store.persons.get("owner", PERSON)
    await store.persons.archive("owner", PERSON, person.revision)
    feed = await store.voice_attributions.feed("owner", after=2)
    assert [(c.seq, c.attribution.attribution_id, c.attribution.revoked, c.attribution.revision)
            for c in feed.changes] == [(3, a.attribution_id, True, 2), (4, b.attribution_id, True, 2)]
    page = await store.voice_attributions.feed("owner", after=0, limit=1)
    assert [c.seq for c in page.changes] == [3] and page.next_after == 3
    assert (await store.voice_attributions.feed("owner", after=4)).next_after == 4
    from sqlalchemy import func, select

    from eidolon_data.schema.voiceprints import VoiceObservationTemplateRow
    async with store._session_factory() as s:
        assert await s.scalar(select(func.count()).select_from(VoiceObservationTemplateRow)) == 0


async def test_verdict_and_feed_http_require_service_auth(store):
    import httpx
    from fastapi import FastAPI

    from eidolon_data.api.routers.voiceprints import create_voiceprint_router
    await enroll(store)
    request = await verdict(store, template=track_sample())
    app = FastAPI()
    app.include_router(create_voiceprint_router(store, "secret"))
    base = "/api/companion-authority/v1/owners/owner/voiceprints"
    auth = {"Authorization": "Bearer secret"}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.post(base + "/verdicts", json=request.model_dump(mode="json"))).status_code == 401
        recorded = await client.post(base + "/verdicts", json=request.model_dump(mode="json"), headers=auth)
        assert recorded.status_code == 200 and recorded.json()["person_id"] == PERSON
        again = request.model_copy(update={"snapshot_revision": "f" * 64})
        assert (await client.post(base + "/verdicts", json=again.model_dump(mode="json"),
                                  headers=auth)).status_code == 409
        feed = await client.get(base + "/attributions", params={"after": 0}, headers=auth)
        assert feed.status_code == 200 and feed.json()["next_after"] == 1
        assert feed.headers["cache-control"] == "no-store"
        served = await client.get(base + "/recognition-candidates",
                                  params={"channel": BOX, "session_id": "s2"}, headers=auth)
        assert [t["channel"] for t in served.json()["persons"][0]["templates"]] == ["mobile", BOX]
