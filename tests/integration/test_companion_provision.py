"""Adding a second Companion, and the properties that make it safe to retry.

The write is small. What matters is what it must not do:

- create two Companions when a phone asks twice;
- accept a different request under an operation id that has already been used;
- create a second memory realm — an Owner has one and every Companion shares it
  (§4.4), so the plan's exit condition is literally "creating a second Companion
  produces no new Realm";
- disturb the realm that already exists, or the default that already points
  somewhere.
"""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import func, select

from eidolon_data import DataSettings, DataStore
from eidolon_data.api.workspace_authority import create_app
from eidolon_data.schema import MemoryRealmRow

pytestmark = [pytest.mark.asyncio]

TOKEN = "workspace-authority-token-0001"
PATH = "/api/workspace-authority/v1/owners/{owner}/companion-provisions/{operation}"
OPERATION = "32c421a3-e0df-40f9-8f75-68745ae39d81"
OTHER_OPERATION = "7c1f0c2e-6d34-4f0a-9a2b-0e6c9a55e321"


@pytest.fixture
async def client(tmp_path):
    settings = DataSettings(sqlite_path=str(tmp_path / "provision.sqlite3"))
    writer = DataStore.open(settings)
    await writer.init_schema()
    app = create_app(settings, service_token=TOKEN)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://data.test"
        ) as http,
    ):
        yield http, writer
    await writer.close()


def _auth() -> dict[str, str]:
    return {"Authorization": f"Bearer {TOKEN}"}


async def _owner_with_one(store: DataStore, owner_id: str = "owner-1"):
    await store.owner_commands.create_owner(owner_id=owner_id, display_name="Manson")
    # Identifiers are per Owner: two Owners seeded with the same ones would
    # collide in the store and the test would be measuring its own fixture.
    return await store.companion_workspaces.provision_workspace(
        owner_id=owner_id,
        companion_id=f"c-first-{owner_id}",
        companion_display_name="小忆",
        genome_id=f"genome-first-{owner_id}",
        realm_id=f"realm-first-{owner_id}",
        kind="conversational",
    )


async def _realms(store: DataStore, owner_id: str = "owner-1") -> int:
    async with store.companions._session_factory() as session:
        return (
            await session.execute(
                select(func.count())
                .select_from(MemoryRealmRow)
                .where(MemoryRealmRow.owner_id == owner_id)
            )
        ).scalar_one()


async def _owner(http: httpx.AsyncClient, owner_id: str = "owner-1") -> dict:
    response = await http.get(
        f"/api/workspace-authority/v1/owners/{owner_id}", headers=_auth()
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_a_second_companion_shares_the_owners_one_memory(client) -> None:
    """The plan's Phase 2 exit condition, asserted by counting realms.

    Not "a new realm is unused" — none is created. A realm is a running process
    somewhere, and one per Companion was the design this plan replaced.
    """
    http, store = client
    first = await _owner_with_one(store)
    assert await _realms(store) == 1

    answered = await http.put(
        PATH.format(owner="owner-1", operation=OPERATION),
        headers=_auth(),
        json={"companion_display_name": "阿力"},
    )

    assert answered.status_code == 200, answered.text
    body = answered.json()
    assert await _realms(store) == 1
    assert body["memory_realm_id"] == first.memory_realm.realm_id
    # Reported, so a caller knows there is no runtime reconcile owed here.
    assert body["memory_realm_created"] is False
    assert body["replayed"] is False


async def test_asking_twice_produces_one_companion(client) -> None:
    """A phone that lost the answer asks again; it must not get a twin.

    Every identifier is derived from the operation id, so the retry addresses
    the rows the first call created rather than making new ones.
    """
    http, store = client
    await _owner_with_one(store)

    first = await http.put(
        PATH.format(owner="owner-1", operation=OPERATION),
        headers=_auth(),
        json={"companion_display_name": "阿力"},
    )
    second = await http.put(
        PATH.format(owner="owner-1", operation=OPERATION),
        headers=_auth(),
        json={"companion_display_name": "阿力"},
    )

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json()["companion"] == second.json()["companion"]
    # The bodies match; only the flag distinguishes "I did this" from "already
    # done", which is what a caller with a side effect of its own needs.
    assert first.json()["replayed"] is False
    assert second.json()["replayed"] is True

    roster = await store.companions.page_for_owner("owner-1", limit=10)
    assert len(roster) == 2


async def test_a_different_request_under_a_used_id_is_a_conflict(client) -> None:
    """Not an overwrite, and not a second Companion either.

    The stored fingerprint is what makes the difference visible. Without it the
    second call would either silently rename the first Companion or create
    another one, and both look like success to the caller.
    """
    http, store = client
    await _owner_with_one(store)
    await http.put(
        PATH.format(owner="owner-1", operation=OPERATION),
        headers=_auth(),
        json={"companion_display_name": "阿力"},
    )

    different = await http.put(
        PATH.format(owner="owner-1", operation=OPERATION),
        headers=_auth(),
        json={"companion_display_name": "另一个名字"},
    )

    assert different.status_code == 409, different.text
    roster = await store.companions.page_for_owner("owner-1", limit=10)
    assert [row.display_name for row in roster] == ["小忆", "阿力"]


async def test_another_owners_operation_is_absent_rather_than_forbidden(
    client,
) -> None:
    """An operation id must not be probeable across Owners."""
    http, store = client
    await _owner_with_one(store)
    await _owner_with_one(store, owner_id="owner-2")
    await http.put(
        PATH.format(owner="owner-2", operation=OPERATION),
        headers=_auth(),
        json={"companion_display_name": "别人的"},
    )

    answered = await http.put(
        PATH.format(owner="owner-1", operation=OPERATION),
        headers=_auth(),
        json={"companion_display_name": "别人的"},
    )

    assert answered.status_code == 404, answered.text


async def test_creating_one_does_not_move_the_default(client) -> None:
    """Adding is not choosing.

    A person who adds a second Eidolon has not said anything about which one
    answers by default, and quietly moving the pointer would change where their
    running conversations go.
    """
    http, store = client
    await _owner_with_one(store)
    before = await _owner(http)

    await http.put(
        PATH.format(owner="owner-1", operation=OPERATION),
        headers=_auth(),
        json={"companion_display_name": "阿力"},
    )

    after = await _owner(http)
    assert (
        after["default_companion_id"] == before["default_companion_id"] == "c-first-owner-1"
    )
    assert after["revision"] == before["revision"], "the Owner aggregate is untouched"


async def test_the_first_companion_of_an_owner_still_becomes_the_default(
    client,
) -> None:
    """The other half of the rule above.

    An Owner with no default-eligible Companion has nothing answering them, so
    the first one to arrive takes the pointer. This route is where a second
    Companion normally arrives, but it is also reachable before any exists.
    """
    http, store = client
    await store.owner_commands.create_owner(owner_id="owner-1", display_name="Manson")

    answered = await http.put(
        PATH.format(owner="owner-1", operation=OPERATION),
        headers=_auth(),
        json={"companion_display_name": "第一个"},
    )

    assert answered.status_code == 200, answered.text
    # And here a realm *was* created, so the caller is told a reconcile is owed.
    assert answered.json()["memory_realm_created"] is True
    assert (await _owner(http))["default_companion_id"] == answered.json()["companion"][
        "companion_id"
    ]


async def test_a_guard_does_not_take_the_pointer(client) -> None:
    """Guard belongs to another product line; it must never answer by default."""
    http, store = client
    await store.owner_commands.create_owner(owner_id="owner-1", display_name="Manson")

    answered = await http.put(
        PATH.format(owner="owner-1", operation=OPERATION),
        headers=_auth(),
        json={"companion_display_name": "守卫", "kind": "guard"},
    )

    assert answered.status_code == 200, answered.text
    assert (await _owner(http))["default_companion_id"] is None


async def test_two_operations_make_two_companions(client) -> None:
    """Idempotency is per operation, not per Owner.

    A rule that collapsed distinct operations would make the second Eidolon
    unaddable, which is the whole point of this phase.
    """
    http, store = client
    await _owner_with_one(store)

    for operation, name in ((OPERATION, "阿力"), (OTHER_OPERATION, "小南")):
        answered = await http.put(
            PATH.format(owner="owner-1", operation=operation),
            headers=_auth(),
            json={"companion_display_name": name},
        )
        assert answered.status_code == 200, answered.text

    roster = await store.companions.page_for_owner("owner-1", limit=10)
    assert [row.display_name for row in roster] == ["小忆", "阿力", "小南"]
    assert await _realms(store) == 1


async def test_an_unknown_owner_is_not_created_by_asking(client) -> None:
    http, store = client

    answered = await http.put(
        PATH.format(owner="owner-nobody", operation=OPERATION),
        headers=_auth(),
        json={"companion_display_name": "阿力"},
    )

    assert answered.status_code in (400, 404), answered.text


async def test_the_write_needs_the_authority_credential(client) -> None:
    http, store = client
    await _owner_with_one(store)

    anonymous = await http.put(
        PATH.format(owner="owner-1", operation=OPERATION),
        json={"companion_display_name": "阿力"},
    )

    assert anonymous.status_code == 401


async def test_an_unknown_field_is_refused_rather_than_ignored(client) -> None:
    """Otherwise a caller believes it set something it did not.

    ``kind`` is spelled one way; a caller sending ``companion_kind`` would get a
    conversational Companion and no indication that its choice was dropped.
    """
    http, store = client
    await _owner_with_one(store)

    answered = await http.put(
        PATH.format(owner="owner-1", operation=OPERATION),
        headers=_auth(),
        json={"companion_display_name": "阿力", "companion_kind": "guard"},
    )

    assert answered.status_code == 422
