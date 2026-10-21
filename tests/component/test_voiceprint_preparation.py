import base64
import hashlib

import pytest
from eidolon_sdk.biz.voiceprint import (
    EnrollmentCompletion,
    EnrollmentCreate,
    EnrollmentEvaluation,
    ExtractedSample,
    PolicyUpdate,
    VoiceAudioRequest,
    enrollment_evidence_digest,
)

from eidolon_data.services.voiceprints import VoiceprintConflict


async def pending(store):
    await store.owner_commands.create_owner(owner_id="owner")
    await store.companion_workspaces.provision_workspace(
        owner_id="owner", companion_id="a", genome_id="g", realm_id="r", kind="conversational"
    )
    v = store.voiceprints
    e = await v.create_enrollment(
        "owner", "a", EnrollmentCreate(operation_id="one", display_name="我")
    )
    sample = ExtractedSample(
        model_id="test",
        weights_hash="a" * 64,
        preprocessing_version="v1",
        vector=[1, 0],
        speech_ms=4000,
    )
    for i in range(3):
        await v.add_sample("owner", "a", e.enrollment_id, str(i), sample)
    pcm = b"\x01\x00" * 32000
    completion = EnrollmentCompletion(
        evaluation=EnrollmentEvaluation(
            evidence_digest=enrollment_evidence_digest({str(i): sample for i in range(3)}),
            recording_sha256=hashlib.sha256(pcm).hexdigest(),
            template=sample,
            trial=sample,
            registration_min_similarity=1,
            trial_min_similarity=1,
        ),
        recording=VoiceAudioRequest(pcm_base64=base64.b64encode(pcm).decode()),
    )
    return v, e, completion


async def test_save_is_atomic_and_does_not_bind_or_enable(store):
    v, e, completion = await pending(store)
    p = await v.complete("owner", "a", e.enrollment_id, completion)
    view = await v.view("owner", "a")
    assert view.allowed_profile_ids == []
    assert view.mode == "off"
    assert p.validation_state == "unvalidated"
    assert p.recording_available
    assert "vector" not in view.model_dump_json()
    from eidolon_data.schema.voiceprints import VoiceEnrollmentRow

    async with store._session_factory() as session:
        row = await session.get(VoiceEnrollmentRow, e.enrollment_id)
        assert len(row.samples_json["registration"]) == 3
        assert row.samples_json["evaluation"]["validation_state"] == "unvalidated"
    assert (await v.complete("owner", "a", e.enrollment_id, completion)) == p
    with pytest.raises(VoiceprintConflict, match="not available"):
        await v.set_policy(
            "owner", "a", PolicyUpdate(mode="allowlist", expected_revision=view.revision)
        )


@pytest.mark.parametrize("field", ["evidence_digest", "recording_sha256"])
async def test_stale_evaluation_or_wrong_recording_cannot_save(store, field):
    v, e, completion = await pending(store)
    bad = completion.model_copy(
        update={"evaluation": completion.evaluation.model_copy(update={field: "0" * 64})}
    )
    with pytest.raises(VoiceprintConflict):
        await v.complete("owner", "a", e.enrollment_id, bad)
    assert (await v.view("owner", "a")).profiles == []
    assert (await v.enrollment("owner", "a", e.enrollment_id)).state == "collecting"


async def test_concurrent_rerecording_cannot_overwrite_a_newer_saved_profile(store):
    v, first, completion = await pending(store)
    profile = await v.complete("owner", "a", first.enrollment_id, completion)
    replacements = []
    for operation in ("replace-a", "replace-b"):
        e = await v.create_enrollment(
            "owner",
            "a",
            EnrollmentCreate(
                operation_id=operation, display_name=operation, profile_id=profile.profile_id
            ),
        )
        for i in range(3):
            await v.add_sample("owner", "a", e.enrollment_id, str(i), completion.evaluation.trial)
        replacements.append(e)
    saved = await v.complete("owner", "a", replacements[0].enrollment_id, completion)
    with pytest.raises(VoiceprintConflict, match="profile changed"):
        await v.complete("owner", "a", replacements[1].enrollment_id, completion)
    assert (await v.view("owner", "a")).profiles == [saved]
