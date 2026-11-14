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

from eidolon_sdk.biz.speaker_identity import SpeakerIdentityObservation, channel_class
from eidolon_sdk.biz.voiceprint import (
    REGISTRATION_CHANNEL,
    EnrollmentCompletion,
    EnrollmentCreate,
    EnrollmentEvidence,
    EnrollmentSample,
    EnrollmentView,
    ExtractedSample,
    PersonVoiceTemplates,
    SpeakerProfileView,
    TemplateCalibration,
    VoiceAudioRequest,
    VoiceprintCollection,
    VoiceTemplate,
    enrollment_evidence_digest,
)
from pydantic import ValidationError
from sqlalchemy import or_, select, text, update

from eidolon_data.audit import governance_fact
from eidolon_data.schema import OwnerRow
from eidolon_data.schema.persons import PersonRow
from eidolon_data.schema.voiceprints import (
    CandidateVoiceObservationRow,
    SpeakerProfileRow,
    SpeakerRecordingRow,
    VoiceEnrollmentRow,
)


class VoiceprintConflict(ValueError):
    pass


class Voiceprints:
    def __init__(self, sessions, *, discovery_policy=None, promotion_policy=None, candidate_pool_ttl_s=604800):
        self._sessions = sessions
        self._discovery_policy = discovery_policy
        self._promotion_policy = promotion_policy
        self._candidate_pool_ttl_s = candidate_pool_ttl_s

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

    async def recognition_candidates(self, owner, channel=None, session_id=None):
        """One bounded, owner-scoped snapshot; no recordings or new authority.

        With a capture ``channel``, each Person's observation templates on that
        channel come too, except those made in conversation ``session_id``
        itself (design §3.2): a track is never compared with its own template.
        """
        # Cheap indexed check first; healthy reads do not acquire a write lock.
        async with self._sessions() as s:
            await self._owner(s, owner)
            expired = await s.scalar(select(PersonRow.person_id).where(
                *self._expired_candidates(owner)).limit(1))
        if expired is not None:
            from eidolon_data.services.persons import Persons
            async with self._transaction() as s:
                await self._owner(s, owner)
                rows = (await s.scalars(select(PersonRow).where(
                    *self._expired_candidates(owner)).limit(100))).all()
                for row in rows:
                    Persons._retain_candidate(s, row)
        async with self._sessions() as s:
            await self._owner(s, owner)
            return await self._recognition_snapshot(s, owner, channel=channel, session_id=session_id)

    def _expired_candidates(self, owner):
        return (PersonRow.owner_id == owner, PersonRow.maturity == "candidate",
            PersonRow.recognition_state == "active", PersonRow.deleted.is_(False),
            PersonRow.candidate_activity_at <= time.time() - self._candidate_pool_ttl_s)

    async def _recognition_snapshot(self, s, owner, channel=None, session_id=None):
        import json

        from eidolon_sdk.biz.person import PersonProfile, address_as
        from eidolon_sdk.biz.speaker_identity import (
            MAX_RECOGNITION_PERSONS,
            SpeakerCandidate,
            SpeakerCandidates,
        )
        rows = (await s.execute(
            select(PersonRow, SpeakerProfileRow, VoiceEnrollmentRow, CandidateVoiceObservationRow).join(
                SpeakerProfileRow, (SpeakerProfileRow.person_id == PersonRow.person_id)
                & (SpeakerProfileRow.owner_id == PersonRow.owner_id)
            ).outerjoin(
                VoiceEnrollmentRow, VoiceEnrollmentRow.enrollment_id == SpeakerProfileRow.enrollment_id
            ).outerjoin(
                CandidateVoiceObservationRow,
                CandidateVoiceObservationRow.observation_id == SpeakerProfileRow.source_observation_id,
            ).where(PersonRow.owner_id == owner, PersonRow.status == "active",
                    PersonRow.deleted.is_(False), PersonRow.recognition_state == "active",
                    or_(PersonRow.maturity != "candidate",
                        PersonRow.candidate_activity_at > time.time() - self._candidate_pool_ttl_s))
            .order_by(PersonRow.person_id).limit(MAX_RECOGNITION_PERSONS + 1)
        )).all()
        if len(rows) > MAX_RECOGNITION_PERSONS:
            raise VoiceprintConflict("recognition candidate capacity exceeded")
        observed = await _channel_templates(s, owner, channel, session_id, [r[0].person_id for r in rows])
        candidates = []
        for person, profile, enrollment, source in rows:
            supplied = PersonProfile.model_validate(person.profile_json)
            candidates.append(SpeakerCandidate(
                person_id=person.person_id, person_revision=person.revision,
                maturity=person.maturity, is_named=person.is_named,
                address_as=address_as(person.display_name, supplied),
                profile=supplied.model_copy(update={"description": "", "preferences": supplied.preferences[:8]}),
                templates=[VoiceTemplate(profile_id=profile.profile_id,
                    revision=profile.revision, sample=ExtractedSample.model_validate(profile.template_json),
                    calibration=_calibration(profile, enrollment), channel=_channel(profile, source)),
                    *observed.get(person.person_id, [])]))
        payload = {"persons": [p.model_dump(mode="json") for p in candidates],
            "discovery_policy": self._discovery_policy.model_dump(mode="json") if self._discovery_policy else None}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return SpeakerCandidates(owner_id=owner, revision=digest, persons=candidates,
            discovery_policy=self._discovery_policy)

    async def discover_candidate(self, owner, request):
        import json

        from eidolon_sdk.biz.candidate_evidence import CANDIDATE_MIN_SPEECH_MS
        from eidolon_sdk.biz.person import PersonCreate
        from eidolon_sdk.biz.speaker_identity import MAX_RECOGNITION_PERSONS

        from eidolon_data.schema.core import MemoryRealmRow
        from eidolon_data.schema.persons import PersonDeletionRow
        from eidolon_data.services.persons import PersonConflict, Persons, _view

        value, policy = request.observation, self._discovery_policy
        if policy is None or request.policy_version != policy.version:
            raise VoiceprintConflict("candidate discovery policy unavailable")
        # The signed discovery decision is the only exclusion of existing
        # identities; it counts only under the identity policy named here.
        if (value.owner_id != owner or value.status != "compared" or not value.decisions.discovery
                or value.policy_version != policy.identity_policy_version
                or request.sample.speech_ms < CANDIDATE_MIN_SPEECH_MS):
            raise VoiceprintConflict("discovery policy or owner mismatch")
        digest = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        async with self._transaction() as session:
            await self._owner(session, owner)
            prior = await session.scalar(select(CandidateVoiceObservationRow).where(
                CandidateVoiceObservationRow.owner_id == owner,
                CandidateVoiceObservationRow.session_id == value.session_id,
                CandidateVoiceObservationRow.turn_id == value.turn_id))
            if prior is not None:
                if prior.request_digest != digest:
                    raise VoiceprintConflict("discovery source already used")
                person = await self._person(session, owner, prior.person_id)
                profile = await session.get(SpeakerProfileRow, prior.profile_id)
                if person.deleted or person.recognition_state != "active" or profile is None:
                    raise VoiceprintConflict("discovered candidate was revoked")
                return _view(person)
            if value.issued_at > time.time() + 1 or value.expires_at <= time.time():
                raise VoiceprintConflict("expired discovery evidence")
            snapshot = await self._recognition_snapshot(session, owner)
            if snapshot.revision != request.snapshot_revision:
                raise VoiceprintConflict("recognition snapshot changed")
            if len(snapshot.persons) >= MAX_RECOGNITION_PERSONS:
                raise VoiceprintConflict("recognition candidate capacity reached")
            replay = await session.scalar(select(CandidateVoiceObservationRow).where(
                CandidateVoiceObservationRow.owner_id == owner,
                CandidateVoiceObservationRow.audio_sha256 == request.audio_sha256))
            if replay is not None:
                raise VoiceprintConflict("discovery audio already used")
            rows = (await session.scalars(select(CandidateVoiceObservationRow).where(
                CandidateVoiceObservationRow.owner_id == owner,
                CandidateVoiceObservationRow.session_id == value.session_id))).all()
            source = value.source
            for row in rows:
                prior_source = row.observation_json['source']
                if (prior_source['input_id'] == source.input_id and prior_source['generation'] == source.generation
                        and prior_source['start_sample'] < source.end_sample and source.start_sample < prior_source['end_sample']):
                    raise VoiceprintConflict("discovery capture overlaps accepted evidence")
            person_id = hashlib.sha256(json.dumps([owner, value.session_id, value.turn_id]).encode()).hexdigest()[:32]
            if (await session.get(PersonRow, person_id) is not None
                    or await session.get(PersonDeletionRow, person_id) is not None):
                raise VoiceprintConflict("discovery identity already used or deleted")
            if await session.scalar(select(MemoryRealmRow.realm_id).where(MemoryRealmRow.person_id == person_id)) is not None:
                raise VoiceprintConflict("discovery identity has retained memory")
            people = Persons(self._sessions, candidate_pool_ttl_s=self._candidate_pool_ttl_s)
            try:
                await people._candidate_capacity(session, owner)
            except PersonConflict as exc:
                raise VoiceprintConflict(str(exc)) from exc
            person = await people._insert_person(session, owner, PersonCreate(person_id=person_id,
                display_name=f"未命名用户 {person_id[:6]}", maturity="candidate", is_named=False))
            await session.flush()
            observation_id, profile_id = uuid.uuid4().hex, "sp_" + uuid.uuid4().hex
            session.add(CandidateVoiceObservationRow(observation_id=observation_id, owner_id=owner,
                person_id=person_id, session_id=value.session_id, turn_id=value.turn_id,
                audio_sha256=request.audio_sha256, profile_id=profile_id, profile_revision=1,
                request_digest=digest, observation_json=value.model_dump(mode="json")))
            await session.flush()
            session.add(SpeakerProfileRow(profile_id=profile_id, owner_id=owner, person_id=person_id,
                revision=1, enrollment_id=None, origin="observation",
                source_observation_id=observation_id, template_json=request.sample.model_dump(mode="json")))
            return _view(person)

    async def record_candidate_evidence(self, owner, request):
        import json

        from eidolon_sdk.biz.candidate_evidence import (
            CANDIDATE_EVIDENCE_LIMIT,
            CandidateEvidenceReceipt,
        )

        value = request.observation
        # The candidate meeting the candidate_evidence threshold, as signed.
        chosen = value.decided("candidate_evidence")
        if value.owner_id != owner:
            raise VoiceprintConflict("evidence owner mismatch")
        if chosen is None:
            raise VoiceprintConflict("evidence names no candidate")
        digest = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        async with self._transaction() as s:
            person = await self._person(s, owner, chosen.person_id)
            if person.deleted or person.recognition_state != "active":
                raise VoiceprintConflict("candidate is not accepting evidence")
            rows = (await s.scalars(select(CandidateVoiceObservationRow).where(
                CandidateVoiceObservationRow.owner_id == owner,
                CandidateVoiceObservationRow.person_id == person.person_id))).all()
            count = sum(row.profile_id == chosen.profile_id and row.profile_revision == chosen.profile_revision
                and row.observation_json.get("policy_version") == value.policy_version for row in rows)
            previous = await s.scalar(select(CandidateVoiceObservationRow).where(
                CandidateVoiceObservationRow.owner_id == owner,
                CandidateVoiceObservationRow.session_id == value.session_id,
                CandidateVoiceObservationRow.turn_id == value.turn_id))
            if previous is not None:
                if previous.request_digest != digest:
                    raise VoiceprintConflict("turn already has different evidence")
                return CandidateEvidenceReceipt(person_id=person.person_id, person_revision=person.revision,
                    maturity=person.maturity, accepted=False, independent_turns=count)
            if person.maturity != "candidate":
                raise VoiceprintConflict("candidate is already established")
            if value.issued_at > time.time() + 1:
                raise VoiceprintConflict("expired candidate evidence")
            if value.expires_at <= time.time():
                raise VoiceprintConflict("expired candidate evidence")
            current = await self._recognition_snapshot(s, owner)
            if current.revision != request.snapshot_revision or person.revision != chosen.person_revision:
                raise VoiceprintConflict("recognition snapshot changed")
            candidate = next((p for p in current.persons if p.person_id == person.person_id), None)
            template = next((t for t in candidate.templates if t.profile_id == chosen.profile_id
                             and t.revision == chosen.profile_revision), None) if candidate else None
            if template is None or (template.sample.model_id, template.sample.weights_hash,
                    template.sample.preprocessing_version) != (value.model_id, value.weights_hash, value.preprocessing_version):
                raise VoiceprintConflict("candidate template changed")
            replay = await s.scalar(select(CandidateVoiceObservationRow).where(
                CandidateVoiceObservationRow.owner_id == owner,
                CandidateVoiceObservationRow.audio_sha256 == request.audio_sha256))
            if replay is not None:
                raise VoiceprintConflict("audio evidence already counted")
            # Different turn IDs over the same captured interval are not independent.
            session_rows = (await s.scalars(select(CandidateVoiceObservationRow).where(
                CandidateVoiceObservationRow.owner_id == owner,
                CandidateVoiceObservationRow.session_id == value.session_id))).all()
            source = value.source
            for row in session_rows:
                prior = row.observation_json['source']
                if (prior['input_id'] == source.input_id and prior['generation'] == source.generation
                        and prior['start_sample'] < source.end_sample and source.start_sample < prior['end_sample']):
                    raise VoiceprintConflict("overlapping capture already counted")
            if len(rows) >= CANDIDATE_EVIDENCE_LIMIT:
                raise VoiceprintConflict("candidate evidence capacity reached")
            s.add(CandidateVoiceObservationRow(observation_id=uuid.uuid4().hex, owner_id=owner,
                person_id=person.person_id, session_id=value.session_id, turn_id=value.turn_id,
                audio_sha256=request.audio_sha256, profile_id=chosen.profile_id,
                profile_revision=chosen.profile_revision, request_digest=digest,
                observation_json=json.loads(value.model_dump_json())))
            s.add(governance_fact(owner_id=owner, subject_type="person", subject_id=person.person_id,
                action="person.candidate_evidence_recorded", payload={"independent_turns": count + 1}))
            person.candidate_activity_at = max(person.candidate_activity_at or 0, value.issued_at)
            await self._promote_candidate(s, person, rows, value)
            return CandidateEvidenceReceipt(person_id=person.person_id, person_revision=person.revision,
                maturity=person.maturity, accepted=True, independent_turns=count + 1)

    async def _promote_candidate(self, session, person, rows, value):
        from eidolon_data.services.persons import Persons

        policy = self._promotion_policy
        if policy is None:
            return
        chosen = value.decided("candidate_evidence")
        now = time.time()

        # The latest match was checked against a current, complete snapshot in
        # this transaction; the signed per-use decision owns conflicts. Never
        # mix templates, model deployments, or identity policies.
        def counted(observation):
            mine = next((e for e in observation.evidence if e.person_id == person.person_id), None)
            quality = observation.audio_quality
            return (observation.status == "compared" and mine is not None
                and quality is not None and quality.overlap_detected == 0 and quality.speech_ms is not None
                and (mine.profile_id, mine.profile_revision) == (chosen.profile_id, chosen.profile_revision)
                and observation.policy_version == policy.identity_policy_version
                and (observation.model_id, observation.weights_hash, observation.preprocessing_version) == (
                    policy.model_id, policy.weights_hash, policy.preprocessing_version)
                and mine.posterior >= policy.min_posterior
                and now - policy.max_evidence_age_s <= observation.issued_at <= now + 1)

        if not counted(value):
            return
        stored = (_stored_observation(row.observation_json) for row in rows)
        observations = [item for item in stored if item is not None and counted(item)] + [value]
        if (len(observations) < policy.min_turns
                or sum(item.audio_quality.speech_ms for item in observations) < policy.min_total_speech_ms):
            return
        person.maturity = "established"
        person.revision += 1
        # Only explicit Owner policy can prepare the existing Realm. Provisional
        # facts remain unchanged; a manual suspension always takes precedence.
        await Persons.prepare_promoted_memory(session, person)
        session.add(governance_fact(owner_id=person.owner_id, subject_type="person", subject_id=person.person_id,
            action="person.candidate_promoted", payload={"revision": person.revision,
                "policy_version": policy.version, "independent_turns": len(observations)}))

    async def templates(self, owner, person_id):
        async with self._sessions() as s:
            person = await self._person(s, owner, person_id)
            rows = (
                await s.execute(
                    select(SpeakerProfileRow, CandidateVoiceObservationRow)
                    .outerjoin(
                        CandidateVoiceObservationRow,
                        CandidateVoiceObservationRow.observation_id
                        == SpeakerProfileRow.source_observation_id,
                    )
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
                        channel=_channel(p, source),
                    )
                    for p, source in rows
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
            original = await s.scalar(select(SpeakerProfileRow).where(
                SpeakerProfileRow.owner_id == owner, SpeakerProfileRow.person_id == request.person_id))
            if request.profile_id and (original is None or original.profile_id != request.profile_id):
                raise VoiceprintConflict("profile is not this person's current voice")
            payload["_target_revision"] = original.revision if original else 0
            e = VoiceEnrollmentRow(
                enrollment_id=eid,
                owner_id=owner,
                person_id=request.person_id,
                profile_id=original.profile_id if original else "sp_" + uuid.uuid4().hex,
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

    async def add_sample(self, owner, eid, sample_id, sample: EnrollmentSample):
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
                    _compatible(EnrollmentSample.model_validate(other), sample)
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
                key: EnrollmentSample.model_validate(x) for key, x in e.samples_json.items()
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
            current = await s.scalar(select(SpeakerProfileRow).where(
                SpeakerProfileRow.owner_id == owner, SpeakerProfileRow.person_id == e.person_id))
            if (current is not None and current.profile_id != e.profile_id) or (
                (p.revision if p else 0) != e.request_json["_target_revision"]
            ):
                raise VoiceprintConflict("profile changed while recording; refresh and start again")
            if p is None:
                p = SpeakerProfileRow(
                    profile_id=e.profile_id, owner_id=owner, person_id=e.person_id, revision=0
                )
                s.add(p)
            p.template_json = evaluation.template.model_dump(mode="json")
            p.revision += 1
            p.enrollment_id = eid
            p.origin = "enrollment"
            p.source_observation_id = None
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
        revision=p.revision,
        enrollment_id=p.enrollment_id,
        origin=p.origin,
        source_observation_id=p.source_observation_id,
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


def _calibration(profile, enrollment):
    """The same-person windows of the registration that made this exact template."""
    if (profile.origin != "enrollment" or enrollment is None or enrollment.state != "ready"
            or enrollment.profile_id != profile.profile_id):
        return None
    stored = (enrollment.samples_json.get("evaluation") or {}).get("calibration")
    return TemplateCalibration.model_validate(stored) if stored else None


async def _channel_templates(s, owner, channel, session_id, person_ids):
    """Per Person, the latest observation templates on ``channel`` made outside ``session_id``."""
    from eidolon_data.schema.voiceprints import VoiceObservationTemplateRow
    from eidolon_data.services.voice_attributions import MAX_CHANNEL_TEMPLATES

    if channel is None or not person_ids:
        return {}
    query = select(VoiceObservationTemplateRow).where(
        VoiceObservationTemplateRow.owner_id == owner, VoiceObservationTemplateRow.channel == channel,
        VoiceObservationTemplateRow.person_id.in_(person_ids))
    if session_id is not None:
        query = query.where(VoiceObservationTemplateRow.session_id != session_id)
    rows = (await s.scalars(query.order_by(VoiceObservationTemplateRow.updated_at.desc(),
                                           VoiceObservationTemplateRow.template_id))).all()
    out = {}
    for row in rows:
        kept = out.setdefault(row.person_id, [])
        if len(kept) < MAX_CHANNEL_TEMPLATES:
            kept.append(VoiceTemplate(profile_id=row.template_id, revision=row.revision,
                                      sample=ExtractedSample.model_validate(row.sample_json),
                                      channel=row.channel))
    return out


def _channel(profile, source):
    """The capture channel this template was built from.

    Registration captures only through the phone app; an observation template
    keeps the channel of the probe that created it. A receipt that never
    recorded one has no known channel.
    """
    if profile.origin != "observation":
        return REGISTRATION_CHANNEL
    recorded = ((source.observation_json or {}).get("source") or {}) if source is not None else {}
    return recorded.get("channel") or channel_class(None)


def _stored_observation(data):
    """A retained receipt in the current observation shape; any other shape never counts."""
    try:
        return SpeakerIdentityObservation.model_validate(data)
    except ValidationError:
        return None


def _compatible(a, b):
    def identity(x):
        return (x.model_id, x.weights_hash, x.preprocessing_version, len(x.vector))

    if identity(a) != identity(b):
        raise VoiceprintConflict("incompatible embedding model")
