"""Transactional Owner-scoped enrollment and Person voiceprint evidence.

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
    EnrollmentEvidence,
    EnrollmentView,
    ExtractedSample,
    SpeakerProfileView,
    VoiceAudioRequest,
    VoiceTemplate,
    enrollment_evidence_digest,
)
from sqlalchemy import select, text, update

from eidolon_data.audit import governance_fact
from eidolon_data.schema import OwnerRow
from eidolon_data.schema.persons import PersonRow
from eidolon_sdk.biz.voiceprint import VoiceprintCollection, PersonVoiceTemplates
from eidolon_data.schema.voiceprints import (
    SpeakerProfileRow,
    SpeakerRecordingRow,
    VoiceEnrollmentRow,
)


class VoiceprintConflict(ValueError):
    pass


class Voiceprints:
    def __init__(self, sessions):
        self._sessions = sessions

    @asynccontextmanager
    async def _transaction(self):
        async with self._sessions() as s:
            # Serialize evidence replacement and identity changes on SQLite.
            await s.execute(text("BEGIN IMMEDIATE"))
            try:
                yield s
                await s.commit()
            except BaseException:
                await s.rollback()
                raise

    async def _owner(self, s, owner):
        row = await s.get(OwnerRow, owner)
        if row is None or row.status != "active":
            raise LookupError("active owner not found")

    async def _person(self, s, owner, person_id):
        await self._owner(s, owner)
        row = await s.get(PersonRow, person_id) if person_id else None
        if row is None or row.owner_id != owner:
            raise LookupError("person not found for owner")
        if row.status != "active":
            raise VoiceprintConflict("person is inactive")
        return row

    async def view(self, owner):
        async with self._sessions() as s:
            await self._owner(s, owner)
            rows = (
                await s.scalars(
                    select(SpeakerProfileRow)
                    .where(SpeakerProfileRow.owner_id == owner)
                    .order_by(SpeakerProfileRow.profile_id)
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
            return VoiceprintCollection(
                profiles=[_profile(p, p.profile_id in recorded) for p in rows]
            )

    async def assign(self, owner, profile_id, request):
        async with self._transaction() as s:
            await self._person(s, owner, request.person_id)
            profile = await self._profile_owned(s, owner, profile_id)
            if profile.revision != request.expected_revision:
                raise VoiceprintConflict("profile revision changed")
            if profile.person_id is not None and profile.person_id != request.person_id:
                raise VoiceprintConflict(
                    "already assigned; do not silently transfer biometric identity"
                )
            if profile.person_id is None:
                profile.person_id = request.person_id
                profile.revision += 1
                enrollments = (
                    await s.scalars(
                        select(VoiceEnrollmentRow).where(
                            VoiceEnrollmentRow.profile_id == profile_id
                        )
                    )
                ).all()
                for enrollment in enrollments:
                    enrollment.person_id = request.person_id
                self._audit(
                    s, owner, profile_id, "voice_speaker.assigned", {"person_id": request.person_id}
                )
            return _profile(profile, await s.get(SpeakerRecordingRow, profile_id) is not None)

    async def templates(self, owner, person_id):
        async with self._sessions() as s:
            person = await self._person(s, owner, person_id)
            rows = (
                await s.scalars(
                    select(SpeakerProfileRow)
                    .where(
                        SpeakerProfileRow.owner_id == owner,
                        SpeakerProfileRow.person_id == person_id,
                    )
                    .order_by(SpeakerProfileRow.profile_id)
                )
            ).all()
            return PersonVoiceTemplates(
                owner_id=owner,
                person_id=person_id,
                person_revision=person.revision,
                templates=[
                    VoiceTemplate(
                        profile_id=p.profile_id,
                        revision=p.revision,
                        sample=ExtractedSample.model_validate(p.template_json),
                    )
                    for p in rows
                ],
            )

    async def delete_profile(self, owner, profile_id):
        async with self._transaction() as s:
            await self._owner(s, owner)
            p = await s.get(SpeakerProfileRow, profile_id)
            if p is not None and p.owner_id != owner:
                raise LookupError("speaker not found for owner")
            if p is not None:
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
                    s, owner, profile_id, "voice_speaker.deleted", {"profile_id": profile_id}
                )
        return await self.view(owner)

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

    async def create_enrollment(self, owner, request: EnrollmentCreate):
        eid = "ve_" + hashlib.sha256(f"{owner}:{request.operation_id}".encode()).hexdigest()[:40]
        async with self._transaction() as s:
            await self._owner(s, owner)
            await self._person(s, owner, request.person_id)
            e = await s.get(VoiceEnrollmentRow, eid)
            payload = request.model_dump(mode="json")
            if e:
                original = {k: v for k, v in e.request_json.items() if k != "_target_revision"}
                if original != payload:
                    raise VoiceprintConflict("operation id reused with different request")
                return _enrollment(e)
            if request.profile_id:
                original = await self._profile_owned(s, owner, request.profile_id)
                if original.person_id != request.person_id:
                    raise VoiceprintConflict("profile belongs to another person")
                payload["_target_revision"] = original.revision
            e = VoiceEnrollmentRow(
                enrollment_id=eid,
                owner_id=owner,
                person_id=request.person_id,
                profile_id=request.profile_id or "sp_" + uuid.uuid4().hex,
                request_json=payload,
                samples_json={},
                state="collecting",
                expires_at=time.time() + 1800,
            )
            s.add(e)
            return _enrollment(e)

    async def _enrollment_owned(self, s, owner, eid):
        await self._owner(s, owner)
        e = await s.get(VoiceEnrollmentRow, eid)
        if e is None or e.owner_id != owner:
            raise LookupError("enrollment not found")
        return e

    async def enrollment(self, owner, eid):
        async with self._transaction() as s:
            e = await self._enrollment_owned(s, owner, eid)
            if e.state == "collecting" and e.expires_at < time.time():
                e.state, e.samples_json = "expired", {}
            return _enrollment(e)

    async def cancel(self, owner, eid):
        async with self._transaction() as s:
            e = await self._enrollment_owned(s, owner, eid)
            if e.state != "ready":
                e.state, e.samples_json = "cancelled", {}
            return _enrollment(e)

    async def evidence(self, owner, eid):
        async with self._transaction() as s:
            e = await self._enrollment_owned(s, owner, eid)
            self._collecting(e)
            return EnrollmentEvidence(enrollment=_enrollment(e), samples=e.samples_json)

    def _collecting(self, e):
        if e.state != "collecting" or e.expires_at < time.time():
            raise VoiceprintConflict("enrollment is not collecting or has expired")

    async def add_sample(self, owner, eid, sample_id, sample: ExtractedSample):
        async with self._transaction() as s:
            e = await self._enrollment_owned(s, owner, eid)
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

    async def recording(self, owner, profile_id):
        async with self._sessions() as s:
            await self._owner(s, owner)
            await self._profile_owned(s, owner, profile_id)
            row = await s.get(SpeakerRecordingRow, profile_id)
            if row is None:
                raise LookupError("no recording; enroll this voice again to enable playback")
            return VoiceAudioRequest(pcm_base64=base64.b64encode(row.pcm).decode("ascii"))

    async def complete(self, owner, eid, request: EnrollmentCompletion):
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
            e = await self._enrollment_owned(s, owner, eid)
            if e.state == "ready":
                current = await self._profile_owned(s, owner, e.profile_id)
                if current.enrollment_id != eid:
                    raise VoiceprintConflict("enrollment was replaced; refresh profiles")
                return _profile(current, await s.get(SpeakerRecordingRow, e.profile_id) is not None)
            self._collecting(e)
            await self._person(s, owner, e.person_id)
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
                p = SpeakerProfileRow(
                    profile_id=e.profile_id, owner_id=owner, person_id=e.person_id, revision=0
                )
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
            self._audit(
                s, owner, p.profile_id, "voice_speaker.enrolled", {"profile_id": p.profile_id}
            )
            return _profile(p, True)

    @staticmethod
    def _audit(s, owner, profile_id, action, payload):
        s.add(
            governance_fact(
                owner_id=owner,
                subject_type="voiceprint",
                subject_id=profile_id,
                action=action,
                payload=payload,
            )
        )


def _profile(p, recording_available=False, *, validated=False):
    return SpeakerProfileView(
        profile_id=p.profile_id,
        person_id=p.person_id,
        display_name=p.display_name,
        revision=p.revision,
        enrollment_id=p.enrollment_id,
        recording_available=recording_available,
        validation_state="validated" if validated else "unvalidated",
    )


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
