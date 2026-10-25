"""An evaluated release never bypasses current Companion authority."""

import pytest
from eidolon_sdk.biz.voiceprint import PolicyUpdate, VoicePolicyActivation, VoicePolicyApproval
from test_voiceprint_preparation import pending

from eidolon_data.services.voiceprints import VoiceprintConflict


async def prepared(store):
    v, enrollment, completion = await pending(store)
    profile = await v.complete("owner", "a", enrollment.enrollment_id, completion)
    await v.bind("owner", "a", profile.profile_id)
    evidence = await v.policy_evidence("owner", "a")
    approval = VoicePolicyApproval(calibration_digest="c" * 64, evidence_digest=evidence.digest())
    request = VoicePolicyActivation(
        mode="allowlist", expected_revision=evidence.revision, approval=approval
    )
    return v, profile, request


async def test_activation_and_admission_bind_release_policy_and_profile(store):
    v, profile, request = await prepared(store)
    view = await v.set_policy("owner", "a", request)
    assert view.mode == "allowlist" and view.admission_state == "ready"
    assert view.profiles[0].validation_state == "validated"
    snapshot = await v.snapshot("a")
    assert snapshot.calibration_digest == "c" * 64
    assert await v.admit(
        "a",
        snapshot.revision,
        profile.profile_id,
        profile_revision=profile.revision,
        calibration_digest="c" * 64,
    )
    assert not await v.admit("a", snapshot.revision, profile.profile_id)
    assert not await v.admit(
        "a",
        snapshot.revision,
        profile.profile_id,
        profile_revision=profile.revision,
        calibration_digest="d" * 64,
    )
    assert not await v.admit(
        "a",
        snapshot.revision,
        profile.profile_id,
        profile_revision=profile.revision + 1,
        calibration_digest="c" * 64,
    )


async def test_changed_bindings_invalidate_approval_without_silently_turning_off(store):
    v, profile, request = await prepared(store)
    await v.set_policy("owner", "a", request)
    old = await v.snapshot("a")
    view = await v.unbind("owner", "a", profile.profile_id)
    assert view.mode == "allowlist" and view.admission_state == "unvalidated"
    assert not (await v.snapshot("a")).calibration_digest
    assert not await v.admit(
        "a",
        old.revision,
        profile.profile_id,
        profile_revision=profile.revision,
        calibration_digest="c" * 64,
    )


async def test_stale_or_wrong_evidence_cannot_enable(store):
    v, profile, request = await prepared(store)
    bad = request.model_copy(
        update={"approval": request.approval.model_copy(update={"evidence_digest": "0" * 64})}
    )
    with pytest.raises(VoiceprintConflict):
        await v.set_policy("owner", "a", bad)
    await v.unbind("owner", "a", profile.profile_id)
    with pytest.raises(VoiceprintConflict):
        await v.set_policy("owner", "a", request)
    with pytest.raises(LookupError):
        await v.policy_evidence("other", "a")


async def test_off_clears_release_and_cannot_reuse_old_approval(store):
    v, _, request = await prepared(store)
    view = await v.set_policy("owner", "a", request)
    await v.set_policy("owner", "a", PolicyUpdate(mode="off", expected_revision=view.revision))
    assert not (await v.snapshot("a")).calibration_digest
    with pytest.raises(VoiceprintConflict):
        await v.set_policy("owner", "a", request)


async def test_confirmation_cannot_move_to_another_companion(store):
    v, profile, request = await prepared(store)
    await store.companion_workspaces.provision_workspace(
        owner_id="owner", companion_id="b", genome_id="gb", realm_id="rb", kind="conversational"
    )
    await v.bind("owner", "b", profile.profile_id)
    with pytest.raises(VoiceprintConflict, match="changed"):
        await v.set_policy("owner", "b", request)
    evidence = await v.policy_evidence("owner", "b")
    own = request.model_copy(
        update={
            "expected_revision": evidence.revision,
            "approval": request.approval.model_copy(update={"evidence_digest": evidence.digest()}),
        }
    )
    assert (await v.set_policy("owner", "b", own)).admission_state == "ready"
    assert (await v.view("owner", "a")).mode == "off"


async def test_rerecording_retires_release_confirmation(store):
    from eidolon_sdk.biz.voiceprint import EnrollmentCompletion, EnrollmentCreate

    v, profile, request = await prepared(store)
    evidence = await v.policy_evidence("owner", "a")
    recording = await v.recording("owner", "a", profile.profile_id)
    await v.set_policy("owner", "a", request)
    old = await v.snapshot("a")
    enrollment = await v.create_enrollment(
        "owner",
        "a",
        EnrollmentCreate(operation_id="replace", display_name="我", profile_id=profile.profile_id),
    )
    evaluation = evidence.profiles[0].evaluation
    for i in range(3):
        await v.add_sample("owner", "a", enrollment.enrollment_id, str(i), evaluation.template)
    saved = await v.complete(
        "owner",
        "a",
        enrollment.enrollment_id,
        EnrollmentCompletion(evaluation=evaluation, recording=recording),
    )
    assert saved.revision > profile.revision
    view = await v.view("owner", "a")
    assert view.mode == "allowlist" and view.admission_state == "unvalidated"
    assert not await v.admit(
        "a",
        old.revision,
        profile.profile_id,
        profile_revision=profile.revision,
        calibration_digest="c" * 64,
    )
