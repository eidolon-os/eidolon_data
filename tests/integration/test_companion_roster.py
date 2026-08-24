"""The Owner's roster, and the one place that says which Companion is default.

Phase 1 of the multi-Companion plan is a read spine: the roster becomes an
observable fact before anything can write to it. The risk in a read spine is
not that it returns too little — it is that it *decides* something. Each test
here pins a decision to exactly one place:

- which Companion is the default → one field on the Owner, named once per page;
- whose Companion this is → one helper in this authority, not the caller;
- where a page ends → a cursor the server issued and can refuse.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport
from jsonschema import Draft202012Validator

from eidolon_data import DataSettings, DataStore
from eidolon_data.api.companion_authority import create_app

pytestmark = [pytest.mark.asyncio]

ROSTER_PAGE_SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "eidolon_data/contracts/schemas/companion/roster-page.schema.json"
)

TOKEN = "companion-roster-token-0001"
ROSTER_TOKEN = "companion-roster-memory-token-0001"


async def _seed(store: DataStore, *, owner_id: str, companions: list[tuple[str, str]]):
    await store.owner_commands.create_owner(owner_id=owner_id, display_name="Owner")
    created = []
    for index, (companion_id, kind) in enumerate(companions):
        # Distinct creation instants, so the sort key is not a tie in every row.
        result = await store.companion_workspaces.provision_workspace(
            owner_id=owner_id,
            companion_id=companion_id,
            companion_display_name=companion_id,
            genome_id=f"genome-{companion_id}",
            realm_id=f"realm-{companion_id}",
            kind=kind,
        )
        created.append(result)
        await _nudge_created_at(store, companion_id, seconds=index)
    return created


async def _nudge_created_at(store: DataStore, companion_id: str, *, seconds: int):
    """Give each row a distinct sort key, so ordering is not a tie everywhere."""
    from sqlalchemy import update

    from eidolon_data.schema import CompanionRow

    async with store.companions._session_factory() as session, session.begin():
        await session.execute(
            update(CompanionRow)
            .where(CompanionRow.companion_id == companion_id)
            .values(created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=seconds))
        )


@pytest.fixture
async def client(tmp_path):
    settings = DataSettings(sqlite_path=str(tmp_path / "roster.sqlite3"))
    writer = DataStore.open(settings)
    await writer.init_schema()
    app = create_app(settings, service_token=TOKEN, memory_roster_token=ROSTER_TOKEN)
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


async def test_the_roster_names_the_default_once_for_the_page(client) -> None:
    """The property this design exists for.

    A boolean on every row would make "two rows both claim to be the default" a
    representable state. The pointer appears once, so it cannot.
    """
    http, store = client
    await _seed(store, owner_id="owner-1", companions=[("c-a", "conversational"), ("c-b", "specialist")])

    body = (
        await http.get("/api/companion-authority/v1/owners/owner-1/companions", headers=_auth())
    ).json()

    Draft202012Validator(
        json.loads(ROSTER_PAGE_SCHEMA.read_text(encoding="utf-8"))
    ).validate(body)
    assert body["operation"] == "companion.roster-page"
    assert body["default_companion_id"] == "c-a"
    assert [row["companion_id"] for row in body["companions"]] == ["c-a", "c-b"]
    for row in body["companions"]:
        assert "is_default" not in row


async def test_no_default_is_a_state_the_roster_reports_rather_than_hides(client) -> None:
    """An Owner whose only Companion is a guard has no default.

    Answering with the first row instead would invent a routing decision this
    authority was never told to make.
    """
    http, store = client
    await _seed(store, owner_id="owner-1", companions=[("guard-1", "guard")])

    body = (
        await http.get("/api/companion-authority/v1/owners/owner-1/companions", headers=_auth())
    ).json()

    assert body["default_companion_id"] is None
    assert [row["companion_id"] for row in body["companions"]] == ["guard-1"]


async def test_order_does_not_encode_which_one_is_the_default(client) -> None:
    """Oldest first, even when the default was created last.

    If the default sorted first, the order would be a second place saying which
    one it is — and a place that can disagree with the pointer.
    """
    http, store = client
    await _seed(store, owner_id="owner-1", companions=[("c-a", "conversational"), ("c-b", "conversational")])
    await store.companion_workspaces.set_default_companion(
        owner_id="owner-1", companion_id="c-b"
    )

    body = (
        await http.get("/api/companion-authority/v1/owners/owner-1/companions", headers=_auth())
    ).json()

    assert [row["companion_id"] for row in body["companions"]] == ["c-a", "c-b"]
    assert body["default_companion_id"] == "c-b"


async def test_the_three_axes_are_reported_as_stored(client) -> None:
    http, store = client
    await _seed(store, owner_id="owner-1", companions=[("c-a", "specialist")])

    row = (
        await http.get("/api/companion-authority/v1/owners/owner-1/companions", headers=_auth())
    ).json()["companions"][0]

    assert (row["kind"], row["lifecycle_state"], row["revision"]) == (
        "specialist",
        "active",
        1,
    )
    assert row["display_name"] == "c-a"


async def test_a_page_boundary_lands_between_rows_and_resumes_there(client) -> None:
    http, store = client
    await _seed(
        store,
        owner_id="owner-1",
        companions=[("c-a", "conversational"), ("c-b", "conversational"), ("c-c", "conversational")],
    )

    first = (
        await http.get(
            "/api/companion-authority/v1/owners/owner-1/companions?limit=2", headers=_auth()
        )
    ).json()
    assert [row["companion_id"] for row in first["companions"]] == ["c-a", "c-b"]
    assert first["next_cursor"]

    second = (
        await http.get(
            "/api/companion-authority/v1/owners/owner-1/companions"
            f"?limit=2&cursor={first['next_cursor']}",
            headers=_auth(),
        )
    ).json()
    assert [row["companion_id"] for row in second["companions"]] == ["c-c"]
    # The last page says so by having no cursor, not by returning fewer rows —
    # a caller cannot tell "fewer" from "the page happened to be short".
    assert second["next_cursor"] is None


async def test_the_default_pointer_travels_on_every_page(client) -> None:
    """Otherwise page two would have to remember page one to render itself."""
    http, store = client
    await _seed(
        store,
        owner_id="owner-1",
        companions=[("c-a", "conversational"), ("c-b", "conversational")],
    )
    await store.companion_workspaces.set_default_companion(
        owner_id="owner-1", companion_id="c-b"
    )

    second = (
        await http.get(
            "/api/companion-authority/v1/owners/owner-1/companions?limit=1"
            f"&cursor={(await http.get('/api/companion-authority/v1/owners/owner-1/companions?limit=1', headers=_auth())).json()['next_cursor']}",
            headers=_auth(),
        )
    ).json()

    assert [row["companion_id"] for row in second["companions"]] == ["c-b"]
    assert second["default_companion_id"] == "c-b"


async def test_an_unreadable_cursor_is_refused_rather_than_restarted(client) -> None:
    http, store = client
    await _seed(store, owner_id="owner-1", companions=[("c-a", "conversational")])

    for cursor in ("not-base64!!", base64.urlsafe_b64encode(b"no-separator").decode()):
        response = await http.get(
            f"/api/companion-authority/v1/owners/owner-1/companions?cursor={cursor}",
            headers=_auth(),
        )
        assert response.status_code == 422


async def test_another_owners_companion_is_absent_not_forbidden(client) -> None:
    """404 rather than 403, so an id cannot be probed for existence."""
    http, store = client
    await _seed(store, owner_id="owner-1", companions=[("c-a", "conversational")])
    await _seed(store, owner_id="owner-2", companions=[("c-b", "conversational")])

    response = await http.get(
        "/api/companion-authority/v1/owners/owner-2/companions/c-a", headers=_auth()
    )
    assert response.status_code == 404

    listed = (
        await http.get("/api/companion-authority/v1/owners/owner-2/companions", headers=_auth())
    ).json()
    assert [row["companion_id"] for row in listed["companions"]] == ["c-b"]


async def test_the_owner_scoped_get_answers_the_same_row_as_the_resolver(client) -> None:
    """Two routes, one row. The difference is who proves ownership."""
    http, store = client
    await _seed(store, owner_id="owner-1", companions=[("c-a", "conversational")])

    scoped = (
        await http.get(
            "/api/companion-authority/v1/owners/owner-1/companions/c-a", headers=_auth()
        )
    ).json()
    resolver = (
        await http.get("/api/companion-authority/v1/companions/c-a", headers=_auth())
    ).json()
    assert scoped == resolver


async def test_both_routes_require_the_service_credential(client) -> None:
    http, store = client
    await _seed(store, owner_id="owner-1", companions=[("c-a", "conversational")])

    for path in (
        "/api/companion-authority/v1/owners/owner-1/companions",
        "/api/companion-authority/v1/owners/owner-1/companions/c-a",
    ):
        # No credential and the wrong credential are different answers: 401 says
        # "identify yourself", 403 says "you did, and it is not this one". The
        # memory roster token is a real credential for a different surface.
        assert (await http.get(path)).status_code == 401
        assert (
            await http.get(path, headers={"Authorization": f"Bearer {ROSTER_TOKEN}"})
        ).status_code == 403


async def test_a_missing_owner_is_not_an_empty_roster(client) -> None:
    """An Owner that does not exist and an Owner with no Companions differ.

    Returning an empty list for both would make "this Host has no such Owner"
    indistinguishable from "this Owner has nobody yet".
    """
    http, _store = client
    assert (
        await http.get(
            "/api/companion-authority/v1/owners/nobody/companions", headers=_auth()
        )
    ).status_code == 404
