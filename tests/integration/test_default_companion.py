"""The first write in the multi-Companion plan: which Companion is the default.

One field on one aggregate, so the interesting part is not the write — it is
what the boundary refuses and how it answers a retry. A phone loses responses,
and a write that is unsafe to repeat forces a client to choose between "ask
again and maybe do it twice" and "give up and leave the person unsure".

- PUT, because it states an end rather than a step;
- compare-and-swap on the Owner revision, so two clients cannot both win;
- a stale revision *whose desired end already holds* is success, not a
  conflict, because that is exactly what a lost response looks like;
- a guard Companion is refused, and a Companion of another Owner is absent
  rather than forbidden.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport
from jsonschema import Draft202012Validator

from eidolon_data import DataSettings, DataStore
from eidolon_data.api.workspace_authority import create_app

pytestmark = [pytest.mark.asyncio]

TOKEN = "workspace-authority-token-0001"
PATH = "/api/workspace-authority/v1/owners/{owner}/default-companion"

#: Published so consumers can gate against it. The Companion identity contract
#: had one and Kernel stayed correct through a change that broke Admin, which
#: had only a pinned copy; the Owner identity had no published schema at all.
OWNER_SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "eidolon_data/contracts/schemas/owner/identity.schema.json"
)


@pytest.fixture
async def client(tmp_path):
    settings = DataSettings(sqlite_path=str(tmp_path / "default.sqlite3"))
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


async def _seed(store: DataStore, *, owner_id: str, companions: list[tuple[str, str]]):
    await store.owner_commands.create_owner(owner_id=owner_id, display_name="Owner")
    for companion_id, kind in companions:
        await store.companion_workspaces.provision_workspace(
            owner_id=owner_id,
            companion_id=companion_id,
            companion_display_name=companion_id,
            genome_id=f"genome-{companion_id}",
            realm_id=f"realm-{companion_id}",
            kind=kind,
        )


async def _owner(http: httpx.AsyncClient, owner_id: str) -> dict:
    response = await http.get(
        f"/api/workspace-authority/v1/owners/{owner_id}", headers=_auth()
    )
    assert response.status_code == 200, response.text
    return response.json()


async def test_the_pointer_moves_and_the_revision_advances(client) -> None:
    http, store = client
    await _seed(
        store,
        owner_id="owner-1",
        companions=[("c-a", "conversational"), ("c-b", "conversational")],
    )
    before = await _owner(http, "owner-1")
    assert before["default_companion_id"] == "c-a"

    answered = await http.put(
        PATH.format(owner="owner-1"),
        headers=_auth(),
        json={"companion_id": "c-b", "expected_revision": before["revision"]},
    )

    assert answered.status_code == 200, answered.text
    assert answered.json()["default_companion_id"] == "c-b"
    # The revision is what the next writer compares against, so it has to move.
    assert answered.json()["revision"] > before["revision"]
    Draft202012Validator(
        json.loads(OWNER_SCHEMA.read_text(encoding="utf-8"))
    ).validate(answered.json())


async def test_saying_it_twice_says_the_same_thing(client) -> None:
    """Idempotent by shape, not by a stored request id.

    Nothing needs remembering: the second call finds the world already as asked
    and says so. An idempotency key would be a second thing to get right.
    """
    http, store = client
    await _seed(
        store,
        owner_id="owner-1",
        companions=[("c-a", "conversational"), ("c-b", "conversational")],
    )
    before = await _owner(http, "owner-1")
    first = await http.put(
        PATH.format(owner="owner-1"),
        headers=_auth(),
        json={"companion_id": "c-b", "expected_revision": before["revision"]},
    )
    second = await http.put(
        PATH.format(owner="owner-1"),
        headers=_auth(),
        json={"companion_id": "c-b", "expected_revision": first.json()["revision"]},
    )

    assert (first.status_code, second.status_code) == (200, 200)
    assert first.json()["revision"] == second.json()["revision"], "no empty write"


async def test_a_lost_response_can_be_retried_with_the_stale_revision(client) -> None:
    """The case a CAS check gets wrong if it only compares numbers.

    The client wrote, the answer never arrived, and it retries with the revision
    it still believes. The end it asked for already holds, so this is success —
    refusing it would leave a re-read as the only safe move, and the person
    watching a spinner would see a conflict for something that worked.
    """
    http, store = client
    await _seed(
        store,
        owner_id="owner-1",
        companions=[("c-a", "conversational"), ("c-b", "conversational")],
    )
    stale = (await _owner(http, "owner-1"))["revision"]
    await http.put(
        PATH.format(owner="owner-1"),
        headers=_auth(),
        json={"companion_id": "c-b", "expected_revision": stale},
    )

    retry = await http.put(
        PATH.format(owner="owner-1"),
        headers=_auth(),
        json={"companion_id": "c-b", "expected_revision": stale},
    )

    assert retry.status_code == 200, retry.text
    assert retry.json()["default_companion_id"] == "c-b"


async def test_a_stale_caller_asking_for_something_else_is_refused(client) -> None:
    """Two clients, and only one of them may win.

    The second one is not wrong about what it wants — it is wrong about what it
    is changing from, which is the thing a person would want to see before it
    happens.
    """
    http, store = client
    await _seed(
        store,
        owner_id="owner-1",
        companions=[
            ("c-a", "conversational"),
            ("c-b", "conversational"),
            ("c-c", "conversational"),
        ],
    )
    stale = (await _owner(http, "owner-1"))["revision"]
    await http.put(
        PATH.format(owner="owner-1"),
        headers=_auth(),
        json={"companion_id": "c-b", "expected_revision": stale},
    )

    loser = await http.put(
        PATH.format(owner="owner-1"),
        headers=_auth(),
        json={"companion_id": "c-c", "expected_revision": stale},
    )

    assert loser.status_code == 409, loser.text
    assert (await _owner(http, "owner-1"))["default_companion_id"] == "c-b"


async def test_a_guard_cannot_become_the_default(client) -> None:
    """It answers a different product's purpose (see the Guard boundary doc).

    Refused at the authority rather than hidden in a client, because a rule only
    one client knows is a rule the next client breaks.
    """
    http, store = client
    await _seed(
        store,
        owner_id="owner-1",
        companions=[("c-a", "conversational"), ("c-guard", "guard")],
    )
    revision = (await _owner(http, "owner-1"))["revision"]

    answered = await http.put(
        PATH.format(owner="owner-1"),
        headers=_auth(),
        json={"companion_id": "c-guard", "expected_revision": revision},
    )

    assert answered.status_code == 400, answered.text
    assert (await _owner(http, "owner-1"))["default_companion_id"] == "c-a"


async def test_another_owners_companion_is_absent_rather_than_forbidden(client) -> None:
    http, store = client
    await _seed(store, owner_id="owner-1", companions=[("c-a", "conversational")])
    await _seed(store, owner_id="owner-2", companions=[("c-x", "conversational")])
    revision = (await _owner(http, "owner-1"))["revision"]

    answered = await http.put(
        PATH.format(owner="owner-1"),
        headers=_auth(),
        json={"companion_id": "c-x", "expected_revision": revision},
    )

    assert answered.status_code == 404, answered.text
    assert (await _owner(http, "owner-1"))["default_companion_id"] == "c-a"


async def test_the_write_needs_the_authority_credential(client) -> None:
    http, store = client
    await _seed(store, owner_id="owner-1", companions=[("c-a", "conversational")])

    anonymous = await http.put(
        PATH.format(owner="owner-1"), json={"companion_id": "c-a"}
    )

    assert anonymous.status_code == 401


async def test_an_unknown_field_is_refused_rather_than_ignored(client) -> None:
    """A caller sending `revision` instead of `expected_revision` must find out.

    Silently ignoring it would turn a compare-and-swap into a blind write, and
    the caller would believe it had protection it does not have.
    """
    http, store = client
    await _seed(store, owner_id="owner-1", companions=[("c-a", "conversational")])

    answered = await http.put(
        PATH.format(owner="owner-1"),
        headers=_auth(),
        json={"companion_id": "c-a", "revision": 1},
    )

    assert answered.status_code == 422


async def test_an_owner_with_no_default_still_matches_the_published_shape(
    client,
) -> None:
    """Null is a real state, so the schema has to admit it.

    An Owner exists before any Companion does. A schema that required the
    pointer would make the correct answer for a fresh Owner unrepresentable,
    and the first consumer to validate strictly would reject it.
    """
    http, store = client
    await store.owner_commands.create_owner(owner_id="owner-1", display_name="Owner")

    body = await _owner(http, "owner-1")

    assert body["default_companion_id"] is None
    Draft202012Validator(json.loads(OWNER_SCHEMA.read_text(encoding="utf-8"))).validate(
        body
    )
