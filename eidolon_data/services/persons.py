"""Single Data authority for OS people; no inference or permission grants."""

from contextlib import asynccontextmanager

from eidolon_sdk.biz.person import PersonView
from sqlalchemy import select, text

from eidolon_data.audit import governance_fact
from eidolon_data.schema.core import OwnerRow
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
            row.profile_json = request.profile.model_dump()
            row.revision += 1
            self._audit(session, row, "person.updated")
            return _view(row)

    async def delete(self, owner_id, person_id, expected_revision):
        async with self._transaction() as session:
            row = await self._person(session, owner_id, person_id)
            self._revision(row, expected_revision)
            self._audit(session, row, "person.deleted")
            await session.delete(row)

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
