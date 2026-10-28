"""Owner-authorized Person images over the existing immutable object store."""
import base64
import binascii
import hashlib
import io
import warnings
from uuid import uuid4
from PIL import Image, ImageOps, UnidentifiedImageError
from eidolon_sdk.biz.person_face import PersonFaceState, PersonFaceImage
from eidolon_data.schema.person_faces import PersonFaceRow
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
                # A replacement cannot retain features derived from an old photo.
                row.model_id = row.preprocessing_version = row.feature_schema_version = row.features_json = None
                self.persons._audit(session, person, "person.face_saved" if data else "person.face_deleted")
                result = self._state(person_id, row)
        except BaseException:
            if new_key:
                self.storage.delete(new_key)
            raise
        if old_key:
            self.storage.delete(old_key)
        return result

    @staticmethod
    def _state(person_id, row):
        return PersonFaceState(person_id=person_id, revision=row.revision if row else 0,
            has_image=bool(row and row.storage_key), sha256=row.sha256 if row else None,
            content_type="image/jpeg" if row and row.storage_key else None)
