"""Attribution: Data records whose voice was heard, by recognition's verdicts (design §3.5).

Data never scores. It checks a verdict against the recognition snapshot it
was made on (the same snapshot the recognizer read, for the channel and
session the voice was heard in), against the Person's revision and the model,
and records the attribution atomically. A verdict that carries a track's
sample also keeps that Person's observation template for the channel
(design §3.2): at most MAX_CHANNEL_TEMPLATES per Person and channel, the
latest kept, never compared within the conversation that made it.

Attributions change only by Owner action (archiving the Person revokes them
and withdraws their templates). Every change takes the Owner's next sequence
number, which orders the attribution feed.

A1 accepts turn verdicts only; accumulated, merge, discovery and promotion
verdicts come with the pools (A3, B2).
"""

from __future__ import annotations

import hashlib
import time
import uuid

from sqlalchemy import delete, func, select

from eidolon_data.audit import governance_fact
from eidolon_data.schema.voiceprints import (
    VoiceAttributionRefRow,
    VoiceAttributionRow,
    VoiceObservationTemplateRow,
)
from eidolon_data.services.voiceprints import VoiceprintConflict

MAX_CHANNEL_TEMPLATES = 3
ACCEPTED_KINDS = frozenset({"turn"})


class VoiceAttributions:
    def __init__(self, voiceprints):
        self._voiceprints = voiceprints

    async def apply_verdict(self, owner, verdict):
        from eidolon_sdk.biz.speaker_identity import channel_class

        if verdict.owner_id != owner:
            raise VoiceprintConflict("verdict owner mismatch")
        if verdict.kind not in ACCEPTED_KINDS:
            raise VoiceprintConflict("verdict kind not accepted")
        ref = verdict.refs[0]
        if verdict.sample is not None and (ref.track_id is None
                                           or verdict.sample_channel == channel_class(None)):
            raise VoiceprintConflict("a template needs its track and a known channel")
        digest = hashlib.sha256(verdict.model_dump_json().encode()).hexdigest()
        voiceprints = self._voiceprints
        async with voiceprints._transaction() as s:
            await voiceprints._owner(s, owner)
            prior = await s.get(VoiceAttributionRefRow, (owner, ref.session_id, ref.turn_id))
            if prior is not None:
                row = await s.get(VoiceAttributionRow, prior.attribution_id)
                if row.verdict_digest != digest:
                    raise VoiceprintConflict("turn already attributed")
                return _attribution(row)
            now = time.time()
            if verdict.issued_at > now + 1 or verdict.expires_at <= now:
                raise VoiceprintConflict("expired verdict")
            snapshot = await voiceprints._recognition_snapshot(
                s, owner, channel=ref.source.channel, session_id=ref.session_id)
            if snapshot.revision != verdict.snapshot_revision:
                raise VoiceprintConflict("recognition snapshot changed")
            person = next((p for p in snapshot.persons if p.person_id == verdict.person_id), None)
            if person is None or person.person_revision != verdict.person_revision:
                raise VoiceprintConflict("attributed Person changed")
            identity = (verdict.model_id, verdict.weights_hash, verdict.preprocessing_version)
            if any((t.sample.model_id, t.sample.weights_hash, t.sample.preprocessing_version) != identity
                   for t in person.templates):
                raise VoiceprintConflict("verdict model does not match the templates")
            row = VoiceAttributionRow(
                attribution_id="va_" + uuid.uuid4().hex, owner_id=owner,
                person_id=verdict.person_id, person_revision=verdict.person_revision,
                source="turn", refs_json=[r.model_dump(mode="json") for r in verdict.refs],
                verdict_digest=digest, revision=1, revoked=False, recorded_at=now,
                change_seq=await _next_seq(s, owner))
            s.add(row)
            await s.flush()
            s.add_all([VoiceAttributionRefRow(owner_id=owner, session_id=r.session_id,
                                              turn_id=r.turn_id, attribution_id=row.attribution_id)
                       for r in verdict.refs])
            if verdict.sample is not None:
                await _keep_template(s, owner, verdict.person_id, verdict.sample_channel, ref,
                                     verdict.sample, row.attribution_id, now)
            s.add(governance_fact(
                owner_id=owner, subject_type="person", subject_id=verdict.person_id,
                action="person.voice_attributed",
                payload={"attribution_id": row.attribution_id, "source": row.source,
                         "turns": len(verdict.refs), "template": verdict.sample is not None}))
            return _attribution(row)

    async def feed(self, owner, after=0, limit=100):
        from eidolon_sdk.biz.voice_attribution import (
            MAX_FEED_CHANGES,
            VoiceAttributionChange,
            VoiceAttributionFeed,
        )

        if after < 0 or not 1 <= limit <= MAX_FEED_CHANGES:
            raise VoiceprintConflict("invalid feed cursor")
        async with self._voiceprints._sessions() as s:
            await self._voiceprints._owner(s, owner)
            rows = (await s.scalars(select(VoiceAttributionRow).where(
                VoiceAttributionRow.owner_id == owner, VoiceAttributionRow.change_seq > after)
                .order_by(VoiceAttributionRow.change_seq).limit(limit))).all()
        changes = [VoiceAttributionChange(seq=r.change_seq, attribution=_attribution(r)) for r in rows]
        return VoiceAttributionFeed(owner_id=owner, changes=changes,
                                    next_after=changes[-1].seq if changes else after)


async def revoke_person(s, owner, person_id):
    """In the caller's transaction: the Owner withdrew this Person; so is every attribution to them."""
    rows = (await s.scalars(select(VoiceAttributionRow).where(
        VoiceAttributionRow.owner_id == owner, VoiceAttributionRow.person_id == person_id,
        VoiceAttributionRow.revoked.is_(False)).order_by(VoiceAttributionRow.change_seq))).all()
    seq = await _next_seq(s, owner)
    for row in rows:
        row.revoked, row.revision, row.change_seq = True, row.revision + 1, seq
        seq += 1
    await s.execute(delete(VoiceObservationTemplateRow).where(
        VoiceObservationTemplateRow.owner_id == owner, VoiceObservationTemplateRow.person_id == person_id))
    return len(rows)


async def _next_seq(s, owner):
    current = await s.scalar(select(func.max(VoiceAttributionRow.change_seq)).where(
        VoiceAttributionRow.owner_id == owner))
    return (current or 0) + 1


async def _keep_template(s, owner, person_id, channel, ref, sample, attribution_id, now):
    row = await s.scalar(select(VoiceObservationTemplateRow).where(
        VoiceObservationTemplateRow.owner_id == owner, VoiceObservationTemplateRow.person_id == person_id,
        VoiceObservationTemplateRow.channel == channel,
        VoiceObservationTemplateRow.session_id == ref.session_id,
        VoiceObservationTemplateRow.track_id == ref.track_id))
    if row is None:
        s.add(VoiceObservationTemplateRow(
            template_id="vt_" + uuid.uuid4().hex, owner_id=owner, person_id=person_id, channel=channel,
            session_id=ref.session_id, track_id=ref.track_id, attribution_id=attribution_id,
            revision=1, sample_json=sample.model_dump(mode="json"), updated_at=now))
    else:
        row.sample_json, row.attribution_id = sample.model_dump(mode="json"), attribution_id
        row.revision, row.updated_at = row.revision + 1, now
    await s.flush()
    kept = (await s.scalars(select(VoiceObservationTemplateRow.template_id).where(
        VoiceObservationTemplateRow.owner_id == owner, VoiceObservationTemplateRow.person_id == person_id,
        VoiceObservationTemplateRow.channel == channel)
        .order_by(VoiceObservationTemplateRow.updated_at.desc(),
                  VoiceObservationTemplateRow.template_id))).all()
    if len(kept) > MAX_CHANNEL_TEMPLATES:
        await s.execute(delete(VoiceObservationTemplateRow).where(
            VoiceObservationTemplateRow.template_id.in_(kept[MAX_CHANNEL_TEMPLATES:])))


def _attribution(row):
    from eidolon_sdk.biz.speaker_identity import VoiceRef
    from eidolon_sdk.biz.voice_attribution import VoiceAttribution

    return VoiceAttribution(
        attribution_id=row.attribution_id, revision=row.revision, owner_id=row.owner_id,
        person_id=row.person_id, person_revision=row.person_revision,
        refs=[VoiceRef.model_validate(r) for r in row.refs_json], source=row.source,
        revoked=row.revoked, recorded_at=row.recorded_at)
