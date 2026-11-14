"""Owner-authorized Person images over the existing immutable object store."""
import base64
import binascii
import hashlib
import io
import json
import warnings
from datetime import UTC, datetime
from uuid import uuid4
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select
from eidolon_sdk.biz.person_face import (MAX_FACE_RECOGNITION_PERSONS, FaceCandidate, FaceCandidates,
    FaceFeatures, FaceTemplate, PersonFaceImage, PersonFaceState)
from eidolon_data.schema.person_faces import PersonFaceRow
from eidolon_data.schema.persons import PersonRow
from eidolon_data.services.persons import PersonConflict

MAX_BYTES = 1024 * 1024
MAX_PIXELS = 4096 * 4096

class PersonFaceInvalid(ValueError):
    pass

def normalize_image(encoded):
    try:
        data = base64.b64decode(encoded, validate=True)
        if not data or len(data) > MAX_BYTES:
            raise PersonFaceInvalid("photo must be at most 1 MiB")
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                if image.format not in {"JPEG", "PNG"} or image.width * image.height > MAX_PIXELS:
                    raise PersonFaceInvalid("use a bounded JPEG or PNG photo")
                image.load()
                clean = ImageOps.exif_transpose(image).convert("RGB")
                clean.thumbnail((1024, 1024))
                output = io.BytesIO()
                clean.save(output, format="JPEG", quality=85)
                return output.getvalue()
    except (ValueError, binascii.Error, OSError, UnidentifiedImageError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise PersonFaceInvalid("invalid or oversized photo") from exc

class PersonFaces:
    def __init__(self, persons, storage):
        self.persons, self.storage = persons, storage

    async def state(self, owner, person_id):
        async with self.persons._sessions() as session:
            await self.persons._person(session, owner, person_id)
            return self._state(person_id, await session.get(PersonFaceRow, person_id))

    async def image(self, owner, person_id):
        async with self.persons._sessions() as session:
            await self.persons._person(session, owner, person_id)
            row = await session.get(PersonFaceRow, person_id)
            data = self.storage.get(row.storage_key) if row and row.storage_key else None
            return PersonFaceImage(revision=row.revision if row else 0,
                image_base64=base64.b64encode(data).decode() if data else None,
                content_type="image/jpeg" if data else None)

    async def replace(self, owner, person_id, request):
        # Check ownership before decoding potentially costly user media.
        await self.state(owner, person_id)
        data = normalize_image(request.image_base64)
        return await self._change(owner, person_id, request.expected_revision, data)

    async def delete(self, owner, person_id, expected_revision):
        return await self._change(owner, person_id, expected_revision, None)

    async def _change(self, owner, person_id, expected_revision, data):
        new_key = None
        old_key = None
        try:
            async with self.persons._transaction() as session:
                person = await self.persons._person(session, owner, person_id)
                if data is not None and person.status != "active":
                    raise PersonConflict("person is inactive")
                row = await session.get(PersonFaceRow, person_id)
                if (row.revision if row else 0) != expected_revision:
                    raise PersonConflict("face profile changed; refresh before retrying")
                if row is None:
                    row = PersonFaceRow(person_id=person_id, owner_id=owner, revision=0)
                    session.add(row)
                old_key = row.storage_key
                if data is not None:
                    new_key = f"person-faces/{uuid4().hex}.jpg"
                    self.storage.put(new_key, data)
                row.storage_key = new_key
                row.sha256 = hashlib.sha256(data).hexdigest() if data else None
                row.revision += 1
                # A replacement cannot retain features derived from an old photo,
                # nor the consent that was given for that photo.
                self._clear_recognition(row)
                self.persons._audit(session, person, "person.face_saved" if data else "person.face_deleted")
                result = self._state(person_id, row)
        except BaseException:
            if new_key:
                self.storage.delete(new_key)
            raise
        if old_key:
            self.storage.delete(old_key)
        return result

    async def enable_recognition(self, owner, person_id, request):
        """Record the Owner's consent together with the feature it authorizes.

        The photo revision is not bumped: it counts photo changes, and the
        feature is bound to the photo it was derived from.
        """
        async with self.persons._transaction() as session:
            person = await self.persons._person(session, owner, person_id)
            if person.status != "active":
                raise PersonConflict("person is inactive")
            row = await session.get(PersonFaceRow, person_id)
            if row is None or not row.storage_key or row.revision != request.expected_revision:
                raise PersonConflict("face profile changed; refresh before retrying")
            features, consent = request.features, request.consent
            row.model_id, row.weights_hash = features.model_id, features.weights_hash
            row.preprocessing_version = features.preprocessing_version
            row.feature_schema_version = features.feature_schema_version
            row.features_json = list(features.vector)
            row.recognition_consent_version = consent.consent_version
            row.recognition_consent_statement = consent.statement
            # The Host's clock says when consent was recorded; a client-sent time is ignored.
            row.recognition_consent_at = datetime.now(UTC).isoformat()
            self.persons._audit(session, person, "person.face_recognition_enabled")
            return self._state(person_id, row)

    async def disable_recognition(self, owner, person_id):
        async with self.persons._transaction() as session:
            person = await self.persons._person(session, owner, person_id)
            row = await session.get(PersonFaceRow, person_id)
            if row is not None and self._clear_recognition(row):
                self.persons._audit(session, person, "person.face_recognition_disabled")
            return self._state(person_id, row)

    async def recognition_candidates(self, owner):
        """Every active, established Person of this Owner with recognition enabled."""
        async with self.persons._sessions() as session:
            await self.persons._owner(session, owner)
            rows = (await session.execute(
                select(PersonRow, PersonFaceRow).join(
                    PersonFaceRow, (PersonFaceRow.person_id == PersonRow.person_id)
                    & (PersonFaceRow.owner_id == PersonRow.owner_id)
                ).where(PersonRow.owner_id == owner, PersonRow.status == "active",
                        PersonRow.deleted.is_(False), PersonRow.maturity == "established",
                        PersonFaceRow.storage_key.is_not(None),
                        PersonFaceRow.features_json.is_not(None),
                        PersonFaceRow.recognition_consent_at.is_not(None))
                .order_by(PersonRow.person_id).limit(MAX_FACE_RECOGNITION_PERSONS + 1)
            )).all()
        # SQL NULL and JSON null are different things in a JSON column; the
        # same predicate as the Owner-facing state decides who is listed.
        rows = [(person, face) for person, face in rows if self._enabled(face)]
        if len(rows) > MAX_FACE_RECOGNITION_PERSONS:
            raise PersonConflict("face recognition candidate capacity exceeded")
        # The name to address them by: what the person asked to be called, else
        # their listed name. Voice recognition names a speaker by the same rule
        # (Channel), so one person is not called two things depending on whether
        # they were heard or seen.
        candidates = [FaceCandidate(person_id=person.person_id,
            display_name=((person.profile_json or {}).get("preferred_name") or "").strip()
            or person.display_name,
            templates=[FaceTemplate(revision=face.revision, features=FaceFeatures(
                model_id=face.model_id, weights_hash=face.weights_hash,
                preprocessing_version=face.preprocessing_version,
                feature_schema_version=face.feature_schema_version, vector=face.features_json))])
            for person, face in rows]
        payload = {"persons": [p.model_dump(mode="json") for p in candidates]}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        return FaceCandidates(owner_id=owner, revision=digest, persons=candidates)

    @staticmethod
    def _clear_recognition(row):
        cleared = any(v is not None for v in (row.features_json, row.model_id, row.weights_hash,
            row.recognition_consent_version, row.recognition_consent_at))
        row.model_id = row.weights_hash = row.preprocessing_version = None
        row.feature_schema_version = row.features_json = None
        row.recognition_consent_version = row.recognition_consent_statement = None
        row.recognition_consent_at = None
        return cleared

    @staticmethod
    def _enabled(row):
        # A feature without its consent, or a consent without a feature, is not recognition.
        return bool(row and row.storage_key and row.features_json
                    and row.recognition_consent_version and row.recognition_consent_at)

    @staticmethod
    def _state(person_id, row):
        enabled = PersonFaces._enabled(row)
        # The feature vector itself is never part of this Owner-facing state.
        return PersonFaceState(person_id=person_id, revision=row.revision if row else 0,
            has_image=bool(row and row.storage_key), sha256=row.sha256 if row else None,
            content_type="image/jpeg" if row and row.storage_key else None,
            recognition_state="enabled" if enabled else "not_configured",
            model_id=row.model_id if enabled else None,
            preprocessing_version=row.preprocessing_version if enabled else None,
            feature_schema_version=row.feature_schema_version if enabled else None,
            recognition_consent_version=row.recognition_consent_version if enabled else None,
            recognition_enabled_at=row.recognition_consent_at if enabled else None)
