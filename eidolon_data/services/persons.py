"""Single Data authority for OS people; no inference or permission grants."""

from contextlib import asynccontextmanager
from uuid import uuid4

from eidolon_sdk.biz.person import PersonView
from sqlalchemy import select, text, update

from eidolon_data.audit import governance_fact
from eidolon_data.schema.core import MemoryRealmRow, OwnerRow
from eidolon_data.schema.persons import PersonRow


class PersonConflict(ValueError):
    pass


class Persons:
    def __init__(self, sessions):
        self._sessions = sessions

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

    async def _person(self, session, owner_id, person_id):
        await self._owner(session, owner_id)
        row = await session.get(PersonRow, person_id)
        if row is None or row.owner_id != owner_id:
            raise LookupError("person not found")
        return row

    async def create(self, owner_id, request):
        async with self._transaction() as session:
            await self._owner(session, owner_id)
            existing = await session.get(PersonRow, request.person_id)
            if existing is not None:
                if existing.owner_id != owner_id:
                    raise PersonConflict("person id unavailable")
                if (
                    existing.display_name != request.display_name
                    or existing.profile_json != request.profile.model_dump()
                ):
                    raise PersonConflict("person id already exists with different data")
                return _view(existing)
            retained = await session.scalar(
                select(MemoryRealmRow).where(MemoryRealmRow.person_id == request.person_id)
            )
            if retained is not None:
                raise PersonConflict("person id has retained memory; explicit recovery required")
            row = PersonRow(
                person_id=request.person_id,
                owner_id=owner_id,
                display_name=request.display_name,
                status="active",
                revision=1,
                profile_json=request.profile.model_dump(),
            )
            session.add(row)
            self._audit(session, row, "person.created")
            return _view(row)

    async def list(self, owner_id):
        async with self._sessions() as session:
            await self._owner(session, owner_id)
            rows = (
                await session.scalars(
                    select(PersonRow)
                    .where(PersonRow.owner_id == owner_id)
                    .order_by(PersonRow.person_id)
                )
            ).all()
            return [_view(row) for row in rows]

    async def get(self, owner_id, person_id):
        async with self._sessions() as session:
            return _view(await self._person(session, owner_id, person_id))

    async def update(self, owner_id, person_id, request):
        async with self._transaction() as session:
            row = await self._person(session, owner_id, person_id)
            self._revision(row, request.expected_revision)
            row.display_name = request.display_name
            row.status = request.status
            if request.status == "inactive":
                await self._deactivate_memory(session, owner_id, person_id)
            row.profile_json = request.profile.model_dump()
            row.revision += 1
            self._audit(session, row, "person.updated")
            return _view(row)

    async def delete(self, owner_id, person_id, expected_revision):
        async with self._transaction() as session:
            row = await self._person(session, owner_id, person_id)
            self._revision(row, expected_revision)
            self._audit(session, row, "person.deleted")
            await self._deactivate_memory(session, owner_id, person_id)
            await session.delete(row)

    async def memory_realm(self, owner_id, person_id, *, create=False):
        """Resolve/create a catalog pointer after service authorization, never from voice."""
        async with self._transaction() as session:
            person = await self._person(session, owner_id, person_id)
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
                realm = MemoryRealmRow(
                    realm_id=f"r_{uuid4().hex}",
                    owner_id=owner_id,
                    person_id=person_id,
                    engine="mempalace",
                    status="active",
                    engine_config_json={},
                    policy_json={"scope": "person"},
                )
                session.add(realm)
                self._audit(session, person, "person.memory_bound")
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

    async def memory_state(self, owner_id, person_id):
        async with self._sessions() as session:
            person = await self._person(session, owner_id, person_id)
            realm = await session.scalar(select(MemoryRealmRow).where(
                MemoryRealmRow.owner_id == owner_id, MemoryRealmRow.person_id == person_id))
            return {
                "owner_id": owner_id, "person_id": person_id,
                "person_revision": person.revision, "person_status": person.status,
                "realm_id": realm.realm_id if realm else None,
                "state": realm.status if realm else "unbound",
            }

    async def suspend_memory(self, owner_id, person_id, expected_revision):
        """Stop memory while retaining the Person and their voice profiles."""
        async with self._transaction() as session:
            person = await self._person(session, owner_id, person_id)
            self._revision(person, expected_revision)
            realm = await session.scalar(select(MemoryRealmRow).where(
                MemoryRealmRow.owner_id == owner_id, MemoryRealmRow.person_id == person_id))
            if realm is None:
                raise LookupError("personal memory realm is not bound")
            if realm.status == "active":
                realm.status = "inactive"
                person.revision += 1
                self._audit(session, person, "person.memory_suspended")
            return {
                "owner_id": owner_id, "person_id": person_id,
                "person_revision": person.revision, "person_status": person.status,
                "realm_id": realm.realm_id, "state": realm.status,
            }

    async def restore_memory(self, owner_id, person_id, expected_revision):
        """Explicit management command; runtime resolution must never call this."""
        async with self._transaction() as session:
            person = await self._person(session, owner_id, person_id)
            self._revision(person, expected_revision)
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


def _view(row):
    return PersonView(
        person_id=row.person_id,
        owner_id=row.owner_id,
        display_name=row.display_name,
        status=row.status,
        revision=row.revision,
        profile=row.profile_json,
    )
