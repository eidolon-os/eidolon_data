"""Transactional speaker enrollment and versioned Companion admission authority.

No audio codecs or ML dependencies live in Data. Small bounded embeddings are
transactional JSON. A successful trial may retain one bounded original PCM
recording for owner-only playback, atomically replaced with its profile.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import time
import uuid
from contextlib import asynccontextmanager

from eidolon_sdk.biz.voiceprint import (
    EnrollmentCompletion,
    EnrollmentCreate,
    EnrollmentEvaluation,
    EnrollmentEvidence,
    EnrollmentView,
    ExtractedSample,
    PolicyUpdate,
    SpeakerProfileView,
    VoiceAudioRequest,
    VoicePolicyApproval,
    VoicePolicyEvidence,
    VoicePolicySnapshot,
    VoicePolicyView,
    VoiceProfileEvidence,
    VoiceTemplate,
    enrollment_evidence_digest,
)
from sqlalchemy import select, text, update

from eidolon_data.audit import governance_fact
from eidolon_data.schema import CompanionRow
from eidolon_data.schema.voiceprints import (
    SpeakerBindingRow,
    SpeakerProfileRow,
    SpeakerRecordingRow,
    VoiceEnrollmentRow,
    VoicePolicyRow,
)


class VoiceprintConflict(ValueError):
    pass


class Voiceprints:
    def __init__(self, sessions):
        self._sessions = sessions

    @asynccontextmanager
    async def _transaction(self):
        async with self._sessions() as s:
            # Serialize read/check/write and admission with revocations on SQLite.
            # A successful admit is the authorization commit boundary, not a
            # reusable grant. Calls after a revocation cannot use old evidence.
            await s.execute(text("BEGIN IMMEDIATE"))
            try:
                yield s
                await s.commit()
            except BaseException:
                await s.rollback()
                raise

    async def _owned(self, s, owner, companion):
        c = await s.get(CompanionRow, companion)
        if c is None or c.owner_id != owner:
            raise LookupError("companion not found for owner")
        return c

    async def _policy(self, s, companion):
        p = await s.get(VoicePolicyRow, companion)
        if p is None:
            p = VoicePolicyRow(companion_id=companion, mode="off", revision=0)
            s.add(p)
            await s.flush()
        return p

    async def _bump(self, s, companion):
        p = await self._policy(s, companion)
        p.revision += 1
        p.approval_json = None

    async def _bound_profiles(self, s, owner, companion):
        return (
            await s.scalars(
                select(SpeakerProfileRow)
                .join(
                    SpeakerBindingRow, SpeakerBindingRow.profile_id == SpeakerProfileRow.profile_id
                )
                .where(
                    SpeakerBindingRow.companion_id == companion, SpeakerProfileRow.owner_id == owner
                )
                .order_by(SpeakerProfileRow.profile_id)
            )
        ).all()

    async def _policy_evidence(self, s, owner, companion, revision):
        rows = await self._bound_profiles(s, owner, companion)
        if not rows:
            raise VoiceprintConflict("add an allowed voice before enabling admission")
        profiles = []
        for row in rows:
            enrollment = await s.get(VoiceEnrollmentRow, row.enrollment_id)
            if enrollment is None or enrollment.state != "ready":
                raise VoiceprintConflict("enrollment evidence unavailable")
            try:
                evaluation = EnrollmentEvaluation.model_validate(
                    enrollment.samples_json.get("evaluation")
                )
            except ValueError:
                raise VoiceprintConflict(
                    "enrollment evidence unavailable; record this voice again"
                ) from None
            if evaluation.template != ExtractedSample.model_validate(row.template_json):
                raise VoiceprintConflict("enrollment template changed")
            profiles.append(
                VoiceProfileEvidence(
                    profile_id=row.profile_id, revision=row.revision, evaluation=evaluation
                )
            )
        return VoicePolicyEvidence(
            owner_id=owner, companion_id=companion, revision=revision, profiles=profiles
        )

    async def policy_evidence(self, owner, companion):
        async with self._transaction() as s:
            await self._owned(s, owner, companion)
            p = await self._policy(s, companion)
            return await self._policy_evidence(s, owner, companion, p.revision)

    async def view(self, owner, companion):
        async with self._sessions() as s:
            await self._owned(s, owner, companion)
            p = await s.get(VoicePolicyRow, companion)
            profiles = (
                await s.scalars(
                    select(SpeakerProfileRow)
                    .where(SpeakerProfileRow.owner_id == owner)
                    .order_by(SpeakerProfileRow.profile_id)
                )
            ).all()
            ids = (
                await s.scalars(
                    select(SpeakerBindingRow.profile_id)
                    .where(SpeakerBindingRow.companion_id == companion)
                    .order_by(SpeakerBindingRow.profile_id)
                )
            ).all()
            recorded = set(
                (
                    await s.scalars(
                        select(SpeakerRecordingRow.profile_id)
                        .join(SpeakerProfileRow)
                        .where(SpeakerProfileRow.owner_id == owner)
                    )
                ).all()
            )
            approval = _approval(p)
            return VoicePolicyView(
                companion_id=companion,
                mode=p.mode if p else "off",
                revision=p.revision if p else 0,
                profiles=[
                    _profile(
                        x,
                        x.profile_id in recorded,
                        validated=bool(approval) and x.profile_id in ids,
                    )
                    for x in profiles
                ],
                allowed_profile_ids=list(ids),
                admission_state="ready" if approval else "unvalidated",
                calibration_digest=approval.calibration_digest if approval else "",
            )

    async def set_policy(self, owner, companion, request: PolicyUpdate):
        async with self._transaction() as s:
            await self._owned(s, owner, companion)
            p = await self._policy(s, companion)
            if p.revision != request.expected_revision:
                raise VoiceprintConflict("policy revision changed; refresh")
            if request.mode == "allowlist":
                approval = getattr(request, "approval", None)
                if approval is None:
                    raise VoiceprintConflict(
                        "voice admission is not available; calibration required"
                    )
                evidence = await self._policy_evidence(s, owner, companion, p.revision)
                if approval.evidence_digest != evidence.digest():
                    raise VoiceprintConflict("voice list or enrollment changed; validate again")
                p.approval_json = approval.model_dump(mode="json")
            else:
                p.approval_json = None
            p.mode = request.mode
            p.revision += 1
            self._audit(s, owner, companion, "voice_policy.changed", {"mode": p.mode})
        return await self.view(owner, companion)

    async def bind(self, owner, companion, profile_id):
        async with self._transaction() as s:
            await self._owned(s, owner, companion)
            await self._profile_owned(s, owner, profile_id)
            if await s.get(SpeakerBindingRow, (companion, profile_id)) is None:
                count = len(
                    (
                        await s.scalars(
                            select(SpeakerBindingRow).where(
                                SpeakerBindingRow.companion_id == companion
                            )
                        )
                    ).all()
                )
                if count >= 5:
                    raise VoiceprintConflict("at most five speakers per companion")
                s.add(SpeakerBindingRow(companion_id=companion, profile_id=profile_id))
                await self._bump(s, companion)
                self._audit(s, owner, companion, "voice_speaker.bound", {"profile_id": profile_id})
        return await self.view(owner, companion)

    async def unbind(self, owner, companion, profile_id):
        async with self._transaction() as s:
            await self._owned(s, owner, companion)
            row = await s.get(SpeakerBindingRow, (companion, profile_id))
            if row:
                await s.delete(row)
                await self._bump(s, companion)
                self._audit(
                    s, owner, companion, "voice_speaker.unbound", {"profile_id": profile_id}
                )
        return await self.view(owner, companion)

    async def delete_profile(self, owner, companion, profile_id):
        async with self._transaction() as s:
            await self._owned(s, owner, companion)
            p = await s.get(SpeakerProfileRow, profile_id)
            if p is not None and p.owner_id != owner:
                raise LookupError("speaker not found for owner")
            if p is not None:
                ids = (
                    await s.scalars(
                        select(SpeakerBindingRow.companion_id).where(
                            SpeakerBindingRow.profile_id == profile_id
                        )
                    )
                ).all()
                for cid in ids:
                    await self._bump(s, cid)
                await s.delete(p)
                # Completed enrollments must not resurrect deleted biometric data.
                es = (
                    await s.scalars(
                        select(VoiceEnrollmentRow).where(
                            VoiceEnrollmentRow.profile_id == profile_id
                        )
                    )
                ).all()
                for e in es:
                    await s.delete(e)
                self._audit(
                    s, owner, companion, "voice_speaker.deleted", {"profile_id": profile_id}
                )
        return await self.view(owner, companion)

    async def _profile_owned(self, s, owner, profile_id):
        p = await s.get(SpeakerProfileRow, profile_id)
        if p is None or p.owner_id != owner:
            raise LookupError("speaker not found for owner")
        return p

    async def expire_enrollments(self):
        """Drop abandoned temporary vectors; invoked by authority maintenance."""
        async with self._transaction() as s:
            result = await s.execute(
                update(VoiceEnrollmentRow)
                .where(
                    VoiceEnrollmentRow.state == "collecting",
                    VoiceEnrollmentRow.expires_at < time.time(),
                )
                .values(state="expired", samples_json={})
            )
            return result.rowcount

    async def create_enrollment(self, owner, companion, request: EnrollmentCreate):
        eid = (
            "ve_"
            + hashlib.sha256(f"{owner}:{companion}:{request.operation_id}".encode()).hexdigest()[
                :40
            ]
        )
        async with self._transaction() as s:
            await self._owned(s, owner, companion)
            e = await s.get(VoiceEnrollmentRow, eid)
            payload = request.model_dump(mode="json")
            if e:
                original = {k: v for k, v in e.request_json.items() if k != "_target_revision"}
                if original != payload:
                    raise VoiceprintConflict("operation id reused with different request")
                return _enrollment(e)
            if request.profile_id:
                original = await self._profile_owned(s, owner, request.profile_id)
                payload["_target_revision"] = original.revision
            e = VoiceEnrollmentRow(
                enrollment_id=eid,
                owner_id=owner,
                companion_id=companion,
                profile_id=request.profile_id or "sp_" + uuid.uuid4().hex,
                request_json=payload,
                samples_json={},
                state="collecting",
                expires_at=time.time() + 1800,
            )
            s.add(e)
            return _enrollment(e)

    async def _enrollment_owned(self, s, owner, companion, eid):
        await self._owned(s, owner, companion)
        e = await s.get(VoiceEnrollmentRow, eid)
        if e is None or e.owner_id != owner or e.companion_id != companion:
            raise LookupError("enrollment not found")
        return e

    async def enrollment(self, owner, companion, eid):
        async with self._transaction() as s:
            e = await self._enrollment_owned(s, owner, companion, eid)
            if e.state == "collecting" and e.expires_at < time.time():
                e.state, e.samples_json = "expired", {}
            return _enrollment(e)

    async def cancel(self, owner, companion, eid):
        async with self._transaction() as s:
            e = await self._enrollment_owned(s, owner, companion, eid)
            if e.state != "ready":
                e.state, e.samples_json = "cancelled", {}
            return _enrollment(e)

    async def evidence(self, owner, companion, eid):
        async with self._transaction() as s:
            e = await self._enrollment_owned(s, owner, companion, eid)
            self._collecting(e)
            return EnrollmentEvidence(enrollment=_enrollment(e), samples=e.samples_json)

    def _collecting(self, e):
        if e.state != "collecting" or e.expires_at < time.time():
            raise VoiceprintConflict("enrollment is not collecting or has expired")

    async def add_sample(self, owner, companion, eid, sample_id, sample: ExtractedSample):
        async with self._transaction() as s:
            e = await self._enrollment_owned(s, owner, companion, eid)
            self._collecting(e)
            samples = dict(e.samples_json)
            value = sample.model_dump(mode="json")
            if sample_id in samples:
                if samples[sample_id] != value:
                    raise VoiceprintConflict("sample id reused with different audio")
            else:
                if len(samples) >= 3:
                    raise VoiceprintConflict("three registration samples already received")
                for other in samples.values():
                    _compatible(ExtractedSample.model_validate(other), sample)
                samples[sample_id] = value
                e.samples_json = samples
            return _enrollment(e)

    async def recording(self, owner, companion, profile_id):
        async with self._sessions() as s:
            await self._owned(s, owner, companion)
            await self._profile_owned(s, owner, profile_id)
            row = await s.get(SpeakerRecordingRow, profile_id)
            if row is None:
                raise LookupError("no recording; enroll this voice again to enable playback")
            return VoiceAudioRequest(pcm_base64=base64.b64encode(row.pcm).decode("ascii"))

    async def complete(self, owner, companion, eid, request: EnrollmentCompletion):
        recording = request.recording
        evaluation = request.evaluation
        try:
            pcm = base64.b64decode(recording.pcm_base64, validate=True)
        except (ValueError, binascii.Error):
            raise VoiceprintConflict("invalid reference recording") from None
        if len(pcm) % 2 or not 48000 <= len(pcm) <= 960000:
            raise VoiceprintConflict("reference recording must be 1.5–30 seconds mono PCM16")
        if hashlib.sha256(pcm).hexdigest() != evaluation.recording_sha256:
            raise VoiceprintConflict("evaluation does not match reference recording")
        async with self._transaction() as s:
            e = await self._enrollment_owned(s, owner, companion, eid)
            if e.state == "ready":
                current = await self._profile_owned(s, owner, e.profile_id)
                if current.enrollment_id != eid:
                    raise VoiceprintConflict("enrollment was replaced; refresh profiles")
                return _profile(current, await s.get(SpeakerRecordingRow, e.profile_id) is not None)
            self._collecting(e)
            samples_by_id = {
                key: ExtractedSample.model_validate(x) for key, x in e.samples_json.items()
            }
            samples = list(samples_by_id.values())
            if len(samples) != 3:
                raise VoiceprintConflict("three registration samples are required")
            if enrollment_evidence_digest(samples_by_id) != evaluation.evidence_digest:
                raise VoiceprintConflict("registration evidence changed; evaluate again")
            for sample in samples:
                _compatible(sample, evaluation.template)
                _compatible(sample, evaluation.trial)
            p = await s.get(SpeakerProfileRow, e.profile_id)
            if e.request_json.get("profile_id") and (
                p is None or p.revision != e.request_json.get("_target_revision")
            ):
                raise VoiceprintConflict("profile changed while recording; refresh and start again")
            if p is None:
                p = SpeakerProfileRow(profile_id=e.profile_id, owner_id=owner, revision=0)
                s.add(p)
            p.display_name = e.request_json["display_name"]
            p.template_json = evaluation.template.model_dump(mode="json")
            p.revision += 1
            p.enrollment_id = eid
            await s.flush()
            previous = await s.get(SpeakerRecordingRow, p.profile_id)
            if previous is None:
                s.add(SpeakerRecordingRow(profile_id=p.profile_id, pcm=pcm))
            else:
                previous.pcm = pcm
            for cid in (
                await s.scalars(
                    select(SpeakerBindingRow.companion_id).where(
                        SpeakerBindingRow.profile_id == p.profile_id
                    )
                )
            ).all():
                await self._bump(s, cid)
            # Keep versioned evidence with its profile's enrollment, not in logs
            # or model assets. Replacing a profile erases its superseded evidence.
            old_enrollments = (
                await s.scalars(
                    select(VoiceEnrollmentRow).where(
                        VoiceEnrollmentRow.profile_id == p.profile_id,
                        VoiceEnrollmentRow.enrollment_id != eid,
                        VoiceEnrollmentRow.state == "ready",
                    )
                )
            ).all()
            for old in old_enrollments:
                old.samples_json = {}
            e.state = "ready"
            e.samples_json = {
                "registration": e.samples_json,
                "evaluation": evaluation.model_dump(mode="json"),
            }
            self._audit(s, owner, companion, "voice_speaker.enrolled", {"profile_id": p.profile_id})
            return _profile(p, True)

    async def snapshot(self, companion):
        async with self._transaction() as s:
            c = await s.get(CompanionRow, companion)
            if c is None:
                raise LookupError("companion not found")
            p = await self._policy(s, companion)
            rows = (
                await s.scalars(
                    select(SpeakerProfileRow)
                    .join(
                        SpeakerBindingRow,
                        SpeakerBindingRow.profile_id == SpeakerProfileRow.profile_id,
                    )
                    .where(
                        SpeakerBindingRow.companion_id == companion,
                        SpeakerProfileRow.owner_id == c.owner_id,
                    )
                )
            ).all()
            approval = _approval(p)
            return VoicePolicySnapshot(
                companion_id=companion,
                owner_id=c.owner_id,
                mode=p.mode,
                revision=p.revision,
                calibration_digest=approval.calibration_digest if approval else "",
                templates=[
                    VoiceTemplate(
                        profile_id=r.profile_id,
                        revision=r.revision,
                        sample=ExtractedSample.model_validate(r.template_json),
                    )
                    for r in rows
                ],
            )

    async def admit(
        self, companion, revision, profile_id, *, profile_revision=None, calibration_digest=""
    ):
        async with self._transaction() as s:
            c = await s.get(CompanionRow, companion)
            if c is None or c.lifecycle_state != "active":
                return False
            p = await self._policy(s, companion)
            if p.revision != revision:
                return False
            if p.mode == "off":
                return True
            approval = _approval(p)
            if approval is None or approval.calibration_digest != calibration_digest:
                return False
            binding = (
                await s.get(SpeakerBindingRow, (companion, profile_id)) if profile_id else None
            )
            profile = await s.get(SpeakerProfileRow, profile_id) if binding else None
            return bool(
                profile is not None
                and profile.owner_id == c.owner_id
                and profile.revision == profile_revision
            )

    @staticmethod
    def _audit(s, owner, companion, action, payload):
        s.add(
            governance_fact(
                owner_id=owner,
                subject_type="companion",
                subject_id=companion,
                action=action,
                payload=payload,
            )
        )


def _profile(p, recording_available=False, *, validated=False):
    return SpeakerProfileView(
        profile_id=p.profile_id,
        display_name=p.display_name,
        revision=p.revision,
        enrollment_id=p.enrollment_id,
        recording_available=recording_available,
        validation_state="validated" if validated else "unvalidated",
    )


def _approval(policy):
    if policy is None or policy.mode != "allowlist" or not policy.approval_json:
        return None
    try:
        return VoicePolicyApproval.model_validate(policy.approval_json)
    except ValueError:
        return None


def _enrollment(e):
    return EnrollmentView(
        enrollment_id=e.enrollment_id,
        profile_id=e.profile_id,
        state=e.state,
        accepted_samples=3 if e.state == "ready" else len(e.samples_json),
        expires_at=e.expires_at,
    )


def _compatible(a, b):
    def identity(x):
        return (x.model_id, x.weights_hash, x.preprocessing_version, len(x.vector))

    if identity(a) != identity(b):
        raise VoiceprintConflict("incompatible embedding model")
