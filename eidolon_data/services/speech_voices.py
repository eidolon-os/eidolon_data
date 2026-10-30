"""A Companion's voice choice and its Owner's own voices.

Data records intent and provenance only. Whether a provider can produce a voice
is Channel's fact; this service never asks (V3). Every provider voice it has
been told about stays tracked until Admin reports that the provider deleted it.
"""

from __future__ import annotations

import base64
import binascii
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from eidolon_sdk.biz.speech_voice import (
    DRAFT_LIMIT,
    DRAFT_TTL_HOURS,
    OWNER_VOICE_LIMIT,
    CompanionSpeechVoice,
    OwnerVoice,
    OwnerVoiceBinding,
    OwnerVoiceConsent,
    OwnerVoiceCreate,
    OwnerVoiceRuntime,
    OwnerVoiceSelection,
    OwnerVoiceUpdate,
    ProviderVoiceTombstone,
    SpeechVoiceRecord,
    SpeechVoiceUpdate,
)
from pydantic import ValidationError
from sqlalchemy import delete, func, select, text

from eidolon_data.audit import governance_fact
from eidolon_data.schema.core import CompanionRow, OwnerRow
from eidolon_data.schema.owner_voices import (
    OwnerVoiceBindingRow,
    OwnerVoiceRow,
    ProviderVoiceTombstoneRow,
)

MAX_WAV_BYTES = 12 * 1024 * 1024


class SpeechVoiceConflict(ValueError):
    """409: a stale revision, or a limit the Owner has reached."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class SpeechVoiceInvalid(ValueError):
    """422: a request this authority will not store."""


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(value: datetime | None) -> datetime | None:
    # SQLite returns naive datetimes; everything stored here is UTC.
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _wav(encoded: str, what: str) -> bytes:
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SpeechVoiceInvalid(f"{what} is not base64") from exc
    if not data[:4] == b"RIFF" or data[8:12] != b"WAVE" or len(data) > MAX_WAV_BYTES:
        raise SpeechVoiceInvalid(f"{what} is not a WAV file within limits")
    return data


def _binding(row: OwnerVoiceBindingRow) -> OwnerVoiceBinding:
    return OwnerVoiceBinding(
        provider=row.provider, model=row.model, provider_voice_id=row.provider_voice_id,
        status=row.status, status_detail=row.status_detail,
        created_at=_aware(row.created_at), last_checked_at=_aware(row.last_checked_at),
    )


def _voice(row: OwnerVoiceRow, bindings: list[OwnerVoiceBindingRow],
           used_by: list[str] | None = None) -> OwnerVoice:
    return OwnerVoice(
        voice_asset_id=row.voice_asset_id, owner_id=row.owner_id, name=row.name, method=row.method,
        state=row.state, revision=row.revision, operation_id=row.create_operation_id,
        used_by=used_by or [], design_prompt=row.design_prompt,
        design_preview_text=row.design_preview_text,
        consent=OwnerVoiceConsent.model_validate(row.consent_json) if row.consent_json else None,
        has_reference=row.reference_storage_key is not None,
        has_preview=row.preview_storage_key is not None,
        bindings=[_binding(b) for b in sorted(bindings, key=lambda b: (b.provider, b.model))],
        created_at=_aware(row.created_at), updated_at=_aware(row.updated_at),
        draft_expires_at=_aware(row.draft_expires_at),
    )


def _stored_record(config: dict) -> tuple[SpeechVoiceRecord | None, int]:
    """The stored namespace and its revision; ``None`` when it does not parse."""
    raw = config.get("speech_voice")
    if raw is None:
        return SpeechVoiceRecord(revision=0), 0
    try:
        record = SpeechVoiceRecord.model_validate(raw)
    except ValidationError:
        revision = raw.get("revision") if isinstance(raw, dict) else None
        return None, revision if isinstance(revision, int) and revision >= 0 else 0
    return record, record.revision


def _selects(config: dict, voice_asset_id: str) -> bool:
    record, _ = _stored_record(config)
    selection = record.selection if record else None
    return isinstance(selection, OwnerVoiceSelection) and selection.voice_asset_id == voice_asset_id


class SpeechVoices:
    def __init__(self, sessions, storage) -> None:
        self._sessions = sessions
        self._storage = storage

    @asynccontextmanager
    async def _transaction(self):
        async with self._sessions() as session:
            await session.execute(text("BEGIN IMMEDIATE"))
            try:
                yield session
                await session.commit()
            except BaseException:
                await session.rollback()
                raise

    # ------------------------------------------------------------------ a Companion's choice

    async def companion_voice(self, companion_id: str) -> CompanionSpeechVoice:
        async with self._sessions() as session:
            companion = await session.get(CompanionRow, companion_id)
            if companion is None:
                raise LookupError("companion not found")
            return await self._companion_voice(session, companion)

    async def _companion_voice(self, session, companion: CompanionRow) -> CompanionSpeechVoice:
        record, revision = _stored_record(companion.runtime_config_json or {})
        if record is None:
            return CompanionSpeechVoice(
                companion_id=companion.companion_id, revision=revision, record_state="invalid"
            )
        owner_voice = None
        if isinstance(record.selection, OwnerVoiceSelection):
            row = await session.get(OwnerVoiceRow, record.selection.voice_asset_id)
            if row is not None and row.owner_id == companion.owner_id:
                bindings = await self._bindings(session, row.voice_asset_id)
                owner_voice = OwnerVoiceRuntime(
                    voice_asset_id=row.voice_asset_id, name=row.name, state=row.state,
                    bindings=[_binding(b) for b in bindings],
                )
        return CompanionSpeechVoice(
            companion_id=companion.companion_id, revision=revision,
            selection=record.selection, owner_voice=owner_voice,
        )

    async def choose(self, companion_id: str, update: SpeechVoiceUpdate) -> CompanionSpeechVoice:
        async with self._transaction() as session:
            companion = await session.get(CompanionRow, companion_id)
            if companion is None:
                raise LookupError("companion not found")
            config = dict(companion.runtime_config_json or {})
            record, revision = _stored_record(config)
            if update.expected_revision != revision:
                raise SpeechVoiceConflict("the voice was changed meanwhile", code="speech_voice_changed")
            current = record.selection if record else None
            wanted = update.selection
            if record is not None and _same_choice(current, wanted):
                return await self._companion_voice(session, companion)
            if isinstance(wanted, OwnerVoiceSelection):
                voice = await session.get(OwnerVoiceRow, wanted.voice_asset_id)
                if voice is None or voice.owner_id != companion.owner_id or voice.state != "active":
                    raise SpeechVoiceInvalid("the Owner voice is not available to this Companion")
            if wanted is not None:
                wanted = wanted.model_copy(update={"selected_at": _now()})
            config["speech_voice"] = SpeechVoiceRecord(
                revision=revision + 1, selection=wanted
            ).model_dump(mode="json")
            companion.runtime_config_json = config
            companion.revision += 1
            companion.updated_at = _now()
            session.add(governance_fact(
                owner_id=companion.owner_id, subject_type="companion", subject_id=companion_id,
                action="companion.speech_voice.selected",
                payload={"revision": revision + 1, "source": wanted.source if wanted else None},
            ))
            await session.flush()
            return await self._companion_voice(session, companion)

    # ------------------------------------------------------------------ the Owner's voices

    async def _owner(self, session, owner_id: str) -> None:
        owner = await session.get(OwnerRow, owner_id)
        if owner is None or owner.status != "active":
            raise LookupError("active owner not found")

    async def _bindings(self, session, voice_asset_id: str) -> list[OwnerVoiceBindingRow]:
        result = await session.scalars(
            select(OwnerVoiceBindingRow).where(OwnerVoiceBindingRow.voice_asset_id == voice_asset_id)
        )
        return list(result)

    async def _row(self, session, owner_id: str, voice_asset_id: str) -> OwnerVoiceRow:
        row = await session.get(OwnerVoiceRow, voice_asset_id)
        if row is None or row.owner_id != owner_id:
            raise LookupError("owner voice not found")
        return row

    async def _usage(self, session, owner_id: str) -> dict[str, list[str]]:
        """Which Companions of this Owner chose which of its voices."""
        usage: dict[str, list[str]] = {}
        for companion_id, config in await session.execute(
            select(CompanionRow.companion_id, CompanionRow.runtime_config_json)
            .where(CompanionRow.owner_id == owner_id).order_by(CompanionRow.companion_id)
        ):
            record, _ = _stored_record(config or {})
            if record is not None and isinstance(record.selection, OwnerVoiceSelection):
                usage.setdefault(record.selection.voice_asset_id, []).append(companion_id)
        return usage

    async def owner_voices(self, owner_id: str) -> list[OwnerVoice]:
        async with self._sessions() as session:
            await self._owner(session, owner_id)
            rows = list(await session.scalars(
                select(OwnerVoiceRow).where(OwnerVoiceRow.owner_id == owner_id)
                .order_by(OwnerVoiceRow.created_at)
            ))
            usage = await self._usage(session, owner_id)
            return [_voice(row, await self._bindings(session, row.voice_asset_id),
                           usage.get(row.voice_asset_id)) for row in rows]

    async def owner_voice(self, owner_id: str, voice_asset_id: str) -> OwnerVoice:
        async with self._sessions() as session:
            row = await self._row(session, owner_id, voice_asset_id)
            usage = await self._usage(session, owner_id)
            return _voice(row, await self._bindings(session, voice_asset_id), usage.get(voice_asset_id))

    async def create(self, owner_id: str, request: OwnerVoiceCreate) -> OwnerVoice:
        if request.method == "design" and not (request.design_prompt and request.design_preview_text):
            raise SpeechVoiceInvalid("a designed voice keeps its description and preview text")
        if request.method == "clone" and (request.consent is None or request.reference_wav_base64 is None):
            raise SpeechVoiceInvalid("a cloned voice needs consent and its reference audio")
        reference = _wav(request.reference_wav_base64, "reference") if request.reference_wav_base64 else None
        preview = _wav(request.preview_wav_base64, "preview") if request.preview_wav_base64 else None
        voice_asset_id = f"ov_{uuid4().hex}"
        written: list[str] = []
        try:
            async with self._transaction() as session:
                await self._owner(session, owner_id)
                existing = await session.scalar(select(OwnerVoiceRow).where(
                    OwnerVoiceRow.owner_id == owner_id,
                    OwnerVoiceRow.create_operation_id == request.operation_id,
                ))
                if existing is not None:
                    return _voice(existing, await self._bindings(session, existing.voice_asset_id))
                counts = dict((await session.execute(
                    select(OwnerVoiceRow.state, func.count()).where(OwnerVoiceRow.owner_id == owner_id)
                    .group_by(OwnerVoiceRow.state)
                )).all())
                if sum(counts.values()) >= OWNER_VOICE_LIMIT:
                    raise SpeechVoiceConflict("too many voices", code="owner_voice_limit")
                if request.state == "draft" and counts.get("draft", 0) >= DRAFT_LIMIT:
                    raise SpeechVoiceConflict("too many unsaved voices", code="draft_limit")
                now = _now()
                row = OwnerVoiceRow(
                    voice_asset_id=voice_asset_id, owner_id=owner_id, name=request.name,
                    method=request.method, state=request.state, revision=1,
                    design_prompt=request.design_prompt, design_preview_text=request.design_preview_text,
                    consent_json=request.consent.model_dump(mode="json") if request.consent else None,
                    create_operation_id=request.operation_id, created_at=now, updated_at=now,
                    draft_expires_at=now + timedelta(hours=DRAFT_TTL_HOURS) if request.state == "draft" else None,
                )
                for data, column in ((reference, "reference"), (preview, "preview")):
                    if data is not None:
                        key = f"{owner_id}/owner-voices/{voice_asset_id}/{column}.wav"
                        self._storage.put(key, data)
                        written.append(key)
                        setattr(row, f"{column}_storage_key", key)
                session.add(row)
                await session.flush()  # no ORM relationship orders the binding after its voice
                binding = request.binding
                session.add(OwnerVoiceBindingRow(
                    provider=binding.provider, provider_voice_id=binding.provider_voice_id,
                    voice_asset_id=voice_asset_id, model=binding.model, status=binding.status,
                    status_detail=binding.status_detail, created_at=now, last_checked_at=now,
                ))
                session.add(governance_fact(
                    owner_id=owner_id, subject_type="owner_voice", subject_id=voice_asset_id,
                    action="owner_voice.created", data_classification="sensitive",
                    payload={"method": request.method, "state": request.state,
                             "consent": request.consent.method if request.consent else None},
                ))
                await session.flush()
                return _voice(row, await self._bindings(session, voice_asset_id))
        except BaseException:
            for key in written:
                self._storage.delete(key)
            raise

    async def update(self, owner_id: str, voice_asset_id: str, request: OwnerVoiceUpdate) -> OwnerVoice:
        released: list[str] = []
        written: list[str] = []
        try:
            async with self._transaction() as session:
                row = await self._row(session, owner_id, voice_asset_id)
                if row.revision != request.expected_revision:
                    raise SpeechVoiceConflict("the voice was changed meanwhile", code="owner_voice_changed")
                if request.name is not None:
                    row.name = request.name
                if request.keep and row.state == "draft":
                    row.state, row.draft_expires_at = "active", None
                if request.drop_reference and row.reference_storage_key:
                    released.append(row.reference_storage_key)
                    row.reference_storage_key = None
                if request.reference_wav_base64 is not None:
                    data = _wav(request.reference_wav_base64, "reference")
                    key = f"{owner_id}/owner-voices/{voice_asset_id}/reference-{uuid4().hex[:8]}.wav"
                    self._storage.put(key, data)
                    written.append(key)
                    if row.reference_storage_key:
                        released.append(row.reference_storage_key)
                    row.reference_storage_key = key
                row.revision += 1
                row.updated_at = _now()
                await session.flush()
                voice = _voice(row, await self._bindings(session, voice_asset_id))
        except BaseException:
            for key in written:
                self._storage.delete(key)
            raise
        for key in released:
            self._storage.delete(key)
        return voice

    async def delete(self, owner_id: str, voice_asset_id: str) -> None:
        async with self._transaction() as session:
            row = await self._row(session, owner_id, voice_asset_id)
            keys = await self._retire(session, row, reason="deleted")
            # A Companion that chose it follows the Host again, visibly, rather
            # than pointing at a voice that no longer exists.
            for companion in await session.scalars(
                select(CompanionRow).where(CompanionRow.owner_id == owner_id)
            ):
                config = dict(companion.runtime_config_json or {})
                if _selects(config, voice_asset_id):
                    _, revision = _stored_record(config)
                    config["speech_voice"] = SpeechVoiceRecord(revision=revision + 1).model_dump(mode="json")
                    companion.runtime_config_json = config
                    companion.revision += 1
                    companion.updated_at = _now()
            session.add(governance_fact(
                owner_id=owner_id, subject_type="owner_voice", subject_id=voice_asset_id,
                action="owner_voice.deleted", data_classification="sensitive",
            ))
        for key in keys:
            self._storage.delete(key)

    async def _retire(self, session, row: OwnerVoiceRow, *, reason: str) -> list[str]:
        """Turn every binding into a tombstone and delete the row; returns object keys to drop."""
        for binding in await self._bindings(session, row.voice_asset_id):
            await self._bury(session, binding, reason)
        keys = [k for k in (row.reference_storage_key, row.preview_storage_key) if k]
        await session.delete(row)
        await session.flush()
        return keys

    @staticmethod
    async def _bury(session, binding: OwnerVoiceBindingRow, reason: str) -> None:
        if await session.get(
            ProviderVoiceTombstoneRow, (binding.provider, binding.provider_voice_id)
        ) is None:
            session.add(ProviderVoiceTombstoneRow(
                provider=binding.provider, provider_voice_id=binding.provider_voice_id,
                model=binding.model, reason=reason, created_at=_now(),
            ))
        await session.delete(binding)

    async def bind(self, owner_id: str, voice_asset_id: str, binding: OwnerVoiceBinding) -> OwnerVoice:
        """Record where this voice was made, or how that provider voice is doing now."""
        async with self._transaction() as session:
            row = await self._row(session, owner_id, voice_asset_id)
            now = _now()
            same_model = await session.scalar(select(OwnerVoiceBindingRow).where(
                OwnerVoiceBindingRow.voice_asset_id == voice_asset_id,
                OwnerVoiceBindingRow.provider == binding.provider,
                OwnerVoiceBindingRow.model == binding.model,
            ))
            if same_model is not None and same_model.provider_voice_id == binding.provider_voice_id:
                same_model.status, same_model.status_detail = binding.status, binding.status_detail
                same_model.last_checked_at = now
            else:
                if same_model is not None:
                    await self._bury(session, same_model, "replaced")
                    await session.flush()  # free the model's slot before the new binding takes it
                session.add(OwnerVoiceBindingRow(
                    provider=binding.provider, provider_voice_id=binding.provider_voice_id,
                    voice_asset_id=voice_asset_id, model=binding.model, status=binding.status,
                    status_detail=binding.status_detail, created_at=now, last_checked_at=now,
                ))
            row.updated_at = now
            await session.flush()
            return _voice(row, await self._bindings(session, voice_asset_id))

    async def audio(self, owner_id: str, voice_asset_id: str, kind: str) -> bytes:
        async with self._sessions() as session:
            row = await self._row(session, owner_id, voice_asset_id)
        key = row.reference_storage_key if kind == "reference" else row.preview_storage_key
        if key is None:
            raise LookupError(f"no {kind} audio")
        return self._storage.get(key)

    # ------------------------------------------------------------------ upkeep (Admin's reconciler)

    async def pending(self) -> list[OwnerVoice]:
        """Voices with a binding the provider is still deciding on."""
        async with self._sessions() as session:
            ids = set(await session.scalars(
                select(OwnerVoiceBindingRow.voice_asset_id).where(OwnerVoiceBindingRow.status == "deploying")
            ))
            return [await self._load(session, voice_id) for voice_id in sorted(ids)]

    async def needing_binding(self, provider: str, model: str) -> list[OwnerVoice]:
        """Kept voices some Companion uses that have no binding on this model yet."""
        async with self._sessions() as session:
            chosen = {
                record.selection.voice_asset_id
                for config in await session.scalars(select(CompanionRow.runtime_config_json))
                for record in [_stored_record(config or {})[0]]
                if record is not None and isinstance(record.selection, OwnerVoiceSelection)
            }
            voices = []
            for voice_id in sorted(chosen):
                voice = await self._load(session, voice_id)
                if voice is not None and voice.state == "active" and voice.binding_for(provider, model) is None:
                    voices.append(voice)
            return voices

    async def _load(self, session, voice_asset_id: str) -> OwnerVoice | None:
        row = await session.get(OwnerVoiceRow, voice_asset_id)
        return _voice(row, await self._bindings(session, voice_asset_id)) if row else None

    async def expire_drafts(self) -> int:
        keys: list[str] = []
        async with self._transaction() as session:
            rows = list(await session.scalars(
                select(OwnerVoiceRow).where(OwnerVoiceRow.state == "draft")
            ))
            expired = [r for r in rows if r.draft_expires_at and _aware(r.draft_expires_at) <= _now()]
            for row in expired:
                keys += await self._retire(session, row, reason="draft_expired")
        for key in keys:
            self._storage.delete(key)
        return len(expired)

    async def tombstones(self) -> list[ProviderVoiceTombstone]:
        async with self._sessions() as session:
            rows = await session.scalars(
                select(ProviderVoiceTombstoneRow).order_by(ProviderVoiceTombstoneRow.created_at)
            )
            return [
                ProviderVoiceTombstone(
                    provider=r.provider, model=r.model, provider_voice_id=r.provider_voice_id,
                    reason=r.reason, created_at=_aware(r.created_at),
                )
                for r in rows
            ]

    async def forget(self, provider: str, provider_voice_id: str) -> None:
        """The provider confirmed the voice is gone."""
        async with self._transaction() as session:
            await session.execute(delete(ProviderVoiceTombstoneRow).where(
                ProviderVoiceTombstoneRow.provider == provider,
                ProviderVoiceTombstoneRow.provider_voice_id == provider_voice_id,
            ))

    async def known_provider_voices(self, provider: str) -> set[str]:
        """Every provider voice id this authority tracks for ``provider``, alive or buried."""
        async with self._sessions() as session:
            alive = await session.scalars(select(OwnerVoiceBindingRow.provider_voice_id).where(
                OwnerVoiceBindingRow.provider == provider))
            buried = await session.scalars(select(ProviderVoiceTombstoneRow.provider_voice_id).where(
                ProviderVoiceTombstoneRow.provider == provider))
            return set(alive) | set(buried)


async def retire_owner_voices(session, owner_id: str) -> tuple[int, tuple[str, ...]]:
    """Inside an Owner deletion: tombstone every provider voice; return the count and object keys."""
    rows = list(await session.scalars(select(OwnerVoiceRow).where(OwnerVoiceRow.owner_id == owner_id)))
    keys: list[str] = []
    for row in rows:
        for binding in list(await session.scalars(select(OwnerVoiceBindingRow).where(
            OwnerVoiceBindingRow.voice_asset_id == row.voice_asset_id
        ))):
            await SpeechVoices._bury(session, binding, "owner_deleted")
        keys += [k for k in (row.reference_storage_key, row.preview_storage_key) if k]
    await session.flush()
    return len(rows), tuple(keys)


def _same_choice(current, wanted) -> bool:
    if current is None or wanted is None:
        return current is wanted
    strip = {"selected_at", "display_name"}
    return current.model_dump(exclude=strip) == wanted.model_dump(exclude=strip)
