"""Single Data authority for OS people; no inference or permission grants."""

import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from uuid import uuid4

from eidolon_sdk.biz.person import PersonView
from sqlalchemy import delete, func, select, text, update

from eidolon_data.audit import governance_fact
from eidolon_data.schema.core import MemoryRealmRow, OwnerRow
from eidolon_data.schema.persons import PersonDeletionRow, PersonRow


class PersonConflict(ValueError):
    pass


class Persons:
    def __init__(self, sessions, storage=None, *, candidate_pool_ttl_s=604800):
        self._sessions = sessions
        self._storage = storage
        self._candidate_pool_ttl_s = candidate_pool_ttl_s

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

    async def _owner(self, session, owner_id):
        owner = await session.get(OwnerRow, owner_id)
        if owner is None or owner.status != "active":
            raise LookupError("active owner not found")

    async def _person(self, session, owner_id, person_id, *, include_deleted=False):
        await self._owner(session, owner_id)
        row = await session.get(PersonRow, person_id)
        if row is None or row.owner_id != owner_id or (row.deleted and not include_deleted):
            raise LookupError("person not found")
        return row

    async def create(self, owner_id, request):
        async with self._transaction() as session:
            await self._owner(session, owner_id)
            if await session.get(PersonDeletionRow, request.person_id) is not None:
                raise PersonConflict("person identity has been permanently deleted")
            existing = await session.get(PersonRow, request.person_id)
            if existing is not None:
                if existing.deleted:
                    raise PersonConflict("person is deleted; use explicit restore")
                if existing.owner_id != owner_id:
                    raise PersonConflict("person id unavailable")
                if (
                    existing.maturity != request.maturity
                    or existing.is_named != request.is_named
                    or existing.display_name != request.display_name
                    or existing.profile_json != request.profile.model_dump()
                ):
                    raise PersonConflict("person id already exists with different data")
                return _view(existing)
            retained = await session.scalar(
                select(MemoryRealmRow).where(MemoryRealmRow.person_id == request.person_id)
            )
            if retained is not None:
                raise PersonConflict("person id has retained memory; explicit recovery required")
            if request.maturity == "candidate":
                await self._candidate_capacity(session, owner_id)
            return _view(self._insert_person(session, owner_id, request))

    def _insert_person(self, session, owner_id, request):
        """Caller owns the transaction and has checked identity/capacity."""
        row = PersonRow(
            person_id=request.person_id,
            owner_id=owner_id,
            display_name=request.display_name,
            maturity=request.maturity,
            candidate_activity_at=time.time() if request.maturity == "candidate" else None,
            is_named=request.is_named,
            status="active",
            revision=1,
            profile_json=request.profile.model_dump(),
        )
        session.add(row)
        self._audit(session, row, "person.created")
        self._bind_memory(session, row)
        return row

    async def _candidate_capacity(self, session, owner_id):
        from eidolon_sdk.biz.speaker_identity import MAX_CANDIDATE_PERSONS
        count = await session.scalar(select(func.count()).select_from(PersonRow).where(
            PersonRow.owner_id == owner_id, PersonRow.maturity == "candidate",
            PersonRow.status == "active", PersonRow.deleted.is_(False),
            PersonRow.recognition_state == "active",
            PersonRow.candidate_activity_at > time.time() - self._candidate_pool_ttl_s))
        if count >= MAX_CANDIDATE_PERSONS:
            raise PersonConflict("candidate pool capacity reached")

    async def list(self, owner_id, *, deleted=False):
        async with self._sessions() as session:
            await self._owner(session, owner_id)
            rows = (
                await session.scalars(
                    select(PersonRow)
                    .where(PersonRow.owner_id == owner_id, PersonRow.deleted == deleted)
                    .order_by(PersonRow.person_id)
                )
            ).all()
            pending = set(
                await session.scalars(
                    select(PersonDeletionRow.person_id).where(
                        PersonDeletionRow.owner_id == owner_id, PersonDeletionRow.state == "pending"
                    )
                )
            )
            return [
                _view(row, deletion_state="pending" if row.person_id in pending else "none")
                for row in rows
            ]

    async def get(self, owner_id, person_id):
        async with self._sessions() as session:
            return _view(await self._person(session, owner_id, person_id))

    async def update(self, owner_id, person_id, request):
        async with self._transaction() as session:
            row = await self._person(session, owner_id, person_id)
            self._revision(row, request.expected_revision)
            if (row.maturity == "candidate" and row.status != "active" and request.status == "active"
                    and row.recognition_state == "active"
                    and row.candidate_activity_at > time.time() - self._candidate_pool_ttl_s):
                await self._candidate_capacity(session, owner_id)
            if request.is_named is not None:
                row.is_named = request.is_named
            row.display_name = request.display_name
            row.status = request.status
            if request.status == "inactive":
                await self._deactivate_memory(session, owner_id, person_id)
            row.profile_json = request.profile.model_dump()
            row.revision += 1
            self._audit(session, row, "person.updated")
            return _view(row)

    async def retire_candidate(self, owner_id, person_id, expected_revision):
        """Leave automatic matching without erasing identity, evidence or Realm.

        This is not user archival. No automatic path restores a retained Person.
        """
        async with self._transaction() as session:
            row = await self._person(session, owner_id, person_id)
            self._revision(row, expected_revision)
            if row.maturity != "candidate":
                raise PersonConflict("only candidates can leave the candidate pool")
            if row.recognition_state != "retained":
                self._retain_candidate(session, row)
            return _view(row)

    @staticmethod
    def _retain_candidate(session, row):
        row.recognition_state = "retained"
        row.revision += 1
        Persons._audit(session, row, "person.candidate_retained")

    async def archive(self, owner_id, person_id, expected_revision):
        from eidolon_data.schema.person_faces import PersonFaceRow

        key = None
        async with self._transaction() as session:
            row = await self._person(session, owner_id, person_id)
            self._revision(row, expected_revision)
            self._audit(session, row, "person.archived")
            await self._deactivate_memory(session, owner_id, person_id)
            face = await session.get(PersonFaceRow, person_id)
            key = face.storage_key if face else None
            from eidolon_data.schema.voiceprints import SpeakerProfileRow, VoiceEnrollmentRow

            await session.execute(
                delete(VoiceEnrollmentRow).where(VoiceEnrollmentRow.person_id == person_id)
            )
            await session.execute(
                delete(SpeakerProfileRow).where(SpeakerProfileRow.person_id == person_id)
            )
            if face:
                await session.delete(face)
            row.archived_at = datetime.now(UTC).isoformat()
            row.deleted = True
            row.status = "inactive"
            row.revision += 1
            if key and self._storage:
                self._storage.delete(key)

    async def restore(self, owner_id, person_id, expected_revision):
        async with self._transaction() as session:
            row = await self._person(session, owner_id, person_id, include_deleted=True)
            self._revision(row, expected_revision)
            if row.identity_state == "missing":
                raise PersonConflict(
                    "identity information is missing; this retained memory cannot be restored as a Person"
                )
            if not row.deleted:
                raise PersonConflict("person is not archived")
            realm = await session.scalar(
                select(MemoryRealmRow).where(MemoryRealmRow.person_id == person_id)
            )
            if realm is not None and realm.status == "deleting":
                raise PersonConflict("memory erasure is pending; retry erasure before restoring")
            row.archived_at = None
            row.deleted = False
            row.status = "inactive"
            row.revision += 1
            await self._deactivate_memory(session, owner_id, person_id)
            self._audit(session, row, "person.restored")
            return _view(row)

    async def permanent_delete(self, owner_id, person_id, expected_revision, *, complete=False):
        """Memory erasure precedes atomic removal; retries use a minimal durable receipt."""
        async with self._transaction() as session:
            await self._owner(session, owner_id)
            receipt = await session.get(PersonDeletionRow, person_id)
            if receipt is not None:
                if receipt.owner_id != owner_id:
                    raise LookupError("person not found")
                row = await session.get(PersonRow, person_id)
                allowed = {receipt.revision}
                if row is not None and not complete:
                    allowed.add(row.revision)
                if expected_revision not in allowed:
                    raise PersonConflict("deletion revision does not match")
                if receipt.state == "completed":
                    return _deletion_view(receipt)
            else:
                if complete:
                    raise PersonConflict("deletion has not been requested")
                row = await self._person(session, owner_id, person_id, include_deleted=True)
                self._revision(row, expected_revision)
                if not row.deleted:
                    raise PersonConflict("archive the Person before permanent deletion")
                realm = await session.scalar(
                    select(MemoryRealmRow).where(
                        MemoryRealmRow.owner_id == owner_id, MemoryRealmRow.person_id == person_id
                    )
                )
                if realm is None:
                    raise LookupError("personal memory realm not found")
                receipt = PersonDeletionRow(
                    person_id=person_id,
                    owner_id=owner_id,
                    realm_id=realm.realm_id,
                    revision=expected_revision,
                    state="pending",
                )
                session.add(receipt)
                realm.status = "deleting"
                realm.policy_json = {**(realm.policy_json or {}), "purge": {"state": "pending"}}
                row.revision += 1
                self._audit(session, row, "person.permanent_deletion_requested")
            if complete:
                self._audit(session, row, "person.permanently_deleted")
                await session.execute(
                    delete(MemoryRealmRow).where(
                        MemoryRealmRow.realm_id == receipt.realm_id,
                        MemoryRealmRow.owner_id == owner_id,
                        MemoryRealmRow.person_id == person_id,
                    )
                )
                await session.delete(row)
                receipt.state = "completed"
            return _deletion_view(receipt)

    async def memory_realm(self, owner_id, person_id, *, create=False):
        """Resolve/create a catalog pointer after service authorization, never from voice."""
        async with self._transaction() as session:
            person = await self._person(session, owner_id, person_id)
            if person.maturity != "established":
                raise PersonConflict("candidate memory requires scoped provisional access")
            if person.status != "active":
                raise PersonConflict("person is inactive")
            realm = await session.scalar(
                select(MemoryRealmRow).where(
                    MemoryRealmRow.owner_id == owner_id, MemoryRealmRow.person_id == person_id
                )
            )
            if realm is None:
                if not create:
                    raise LookupError("personal memory realm is not bound")
                realm = self._bind_memory(session, person)
                await session.flush()
            if realm.status != "active":
                raise PersonConflict(
                    "personal memory realm is inactive; explicit recovery required"
                )
            return {
                "realm_id": realm.realm_id,
                "owner_id": owner_id,
                "person_id": person_id,
                "person_revision": person.revision,
                "status": realm.status,
            }

    def _bind_memory(self, session, person):
        """Create the initial personal Realm in the same transaction as its Person."""
        realm = MemoryRealmRow(
            realm_id=f"r_{uuid4().hex}",
            owner_id=person.owner_id,
            person_id=person.person_id,
            engine="mempalace",
            status="active" if person.maturity == "established" else "inactive",
            engine_config_json={},
            policy_json={"scope": "person"},
        )
        session.add(realm)
        self._audit(session, person, "person.memory_bound")
        return realm

    async def memory_state(self, owner_id, person_id):
        async with self._sessions() as session:
            person = await self._person(session, owner_id, person_id)
            realm = await session.scalar(
                select(MemoryRealmRow).where(
                    MemoryRealmRow.owner_id == owner_id, MemoryRealmRow.person_id == person_id
                )
            )
            return {
                "owner_id": owner_id,
                "person_id": person_id,
                "person_revision": person.revision,
                "person_status": person.status,
                "realm_id": realm.realm_id if realm else None,
                "state": realm.status if realm else "unbound",
            }

    async def suspend_memory(self, owner_id, person_id, expected_revision):
        """Stop memory while retaining the Person and their voice profiles."""
        async with self._transaction() as session:
            person = await self._person(session, owner_id, person_id)
            self._revision(person, expected_revision)
            realm = await session.scalar(
                select(MemoryRealmRow).where(
                    MemoryRealmRow.owner_id == owner_id, MemoryRealmRow.person_id == person_id
                )
            )
            if realm is None:
                raise LookupError("personal memory realm is not bound")
            if realm.status == "active":
                realm.status = "inactive"
                person.revision += 1
                self._audit(session, person, "person.memory_suspended")
            return {
                "owner_id": owner_id,
                "person_id": person_id,
                "person_revision": person.revision,
                "person_status": person.status,
                "realm_id": realm.realm_id,
                "state": realm.status,
            }

    async def restore_memory(self, owner_id, person_id, expected_revision):
        """Explicit management command; runtime resolution must never call this."""
        async with self._transaction() as session:
            person = await self._person(session, owner_id, person_id)
            self._revision(person, expected_revision)
            if person.maturity != "established":
                raise PersonConflict("candidate memory requires scoped provisional access")
            if person.status != "active":
                raise PersonConflict("person is inactive")
            realm = await session.scalar(
                select(MemoryRealmRow).where(
                    MemoryRealmRow.owner_id == owner_id,
                    MemoryRealmRow.person_id == person_id,
                )
            )
            if realm is None:
                raise LookupError("personal memory realm is not bound")
            if realm.status != "active":
                realm.status = "active"
                person.revision += 1
                self._audit(session, person, "person.memory_restored")
            return {
                "realm_id": realm.realm_id,
                "owner_id": owner_id,
                "person_id": person_id,
                "person_revision": person.revision,
                "status": realm.status,
            }

    @staticmethod
    async def _deactivate_memory(session, owner_id, person_id):
        await session.execute(
            update(MemoryRealmRow)
            .where(
                MemoryRealmRow.owner_id == owner_id,
                MemoryRealmRow.person_id == person_id,
                MemoryRealmRow.status == "active",
            )
            .values(status="inactive")
        )

    @staticmethod
    def _revision(row, revision):
        if row.revision != revision:
            raise PersonConflict("person revision changed")

    @staticmethod
    def _audit(session, row, action):
        session.add(
            governance_fact(
                owner_id=row.owner_id,
                subject_type="person",
                subject_id=row.person_id,
                action=action,
                payload={"revision": row.revision},
            )
        )


def _view(row, *, deletion_state="none"):
    return PersonView(
        person_id=row.person_id,
        owner_id=row.owner_id,
        display_name=row.display_name,
        status=row.status,
        revision=row.revision,
        profile=row.profile_json,
        recognition_state=row.recognition_state or "active",
        maturity=row.maturity or "established",
        is_named=row.is_named if row.is_named is not None else True,
        identity_state=row.identity_state or "known",
        archived_at=row.archived_at,
        deletion_state=deletion_state,
    )


def _deletion_view(row):
    return {
        "person_id": row.person_id,
        "realm_id": row.realm_id,
        "revision": row.revision,
        "state": row.state,
    }
