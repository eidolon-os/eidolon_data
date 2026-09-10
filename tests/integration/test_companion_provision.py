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
from eidolon_data.api.companion_authority import create_app as create_companion_app
from eidolon_data.api.workspace_authority import create_app
from eidolon_data.schema import CompanionRow, MemoryRealmRow, PersonaGenomeRow

pytestmark = [pytest.mark.asyncio]

TOKEN = "workspace-authority-token-0001"
PATH = "/api/workspace-authority/v1/owners/{owner}/companion-provisions/{operation}"
OPERATION = "32c421a3-e0df-40f9-8f75-68745ae39d81"
OTHER_OPERATION = "7c1f0c2e-6d34-4f0a-9a2b-0e6c9a55e321"


COMPANION_TOKEN = "companion-authority-token-000001"


def companion_authority_app(settings: DataSettings):
    """The persona authority, built on the same store.

    Two apps in one test is the point: the template and the write it precedes
    are now answered by different processes in production, so a test that used
    one app could not see them disagree.
    """

    return create_companion_app(
        settings,
        service_token=COMPANION_TOKEN,
        memory_roster_token="memory-roster-token-0001",
    )


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
        # The settings travel with the client so a test that needs the *other*
        # authority can build it on the same store.
        yield http, writer, settings
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
    http, store, _settings = client
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
    http, store, _settings = client
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
    http, store, _settings = client
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
    http, store, _settings = client
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
    http, store, _settings = client
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
    http, store, _settings = client
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
    http, store, _settings = client
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
    http, store, _settings = client
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
    http, store, _settings = client

    answered = await http.put(
        PATH.format(owner="owner-nobody", operation=OPERATION),
        headers=_auth(),
        json={"companion_display_name": "阿力"},
    )

    assert answered.status_code in (400, 404), answered.text


async def test_the_write_needs_the_authority_credential(client) -> None:
    http, store, _settings = client
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
    http, store, _settings = client
    await _owner_with_one(store)

    answered = await http.put(
        PATH.format(owner="owner-1", operation=OPERATION),
        headers=_auth(),
        json={"companion_display_name": "阿力", "companion_kind": "guard"},
    )

    assert answered.status_code == 422


async def _genome_of(store: DataStore, companion_id: str) -> dict:
    """The genome row this Companion points at, as written."""

    async with store.companions._session_factory() as session:
        companion = await session.get(CompanionRow, companion_id)
        assert companion is not None, companion_id
        row = await session.get(PersonaGenomeRow, companion.current_genome_id)
        assert row is not None, "a provisioned Companion must point at a genome"
        return {"genome": row.genome_json, "source": row.source_json}


async def test_asking_for_nothing_still_writes_a_whole_person(client) -> None:
    """The behaviour that was there before authoring came back, unchanged.

    Somebody who just wants another Eidolon should not have to describe one, so
    an absent ``persona`` is the template — and it must be a *complete* genome,
    not an empty one waiting to be filled in later.
    """

    http, store, _settings = client
    await _owner_with_one(store)
    response = await http.put(
        PATH.format(owner="owner-1", operation=OPERATION),
        json={"companion_display_name": "小南"},
        headers=_auth(),
    )
    assert response.status_code == 200, response.text

    written = await _genome_of(store, response.json()["companion"]["companion_id"])
    assert written["genome"]["constitution"]["name"] == "小南"
    assert written["genome"]["constitution"]["values"], "the template has values"
    assert written["genome"]["character"]["portrait"], "and a portrait"
    assert written["source"]["source_type"] == "companion_provision"
    assert written["genome"]["provenance"]["origin"] == "template"


async def test_what_a_person_wrote_is_what_gets_stored(client) -> None:
    """The whole point of the screen.

    Two Companions used to differ only by name, because the authoring surface
    was removed and every genome came from the same template. This asserts the
    opposite end: the sentences somebody chose reach the row.
    """

    http, store, _settings = client
    await _owner_with_one(store)
    response = await http.put(
        PATH.format(owner="owner-1", operation=OPERATION),
        json={
            "companion_display_name": "小南",
            "persona": {
                "self_concept": "我是一个会记得你说过的话的伙伴",
                "character_portrait": "安静，话不多，但记得住",
                "relationship_narrative": "我们是从一次很长的深夜对话开始的",
                "voice_portrait": "短句，不用感叹号",
                "values": ["诚实"],
                "boundaries": ["不替他做决定"],
                "safety_boundaries": ["不提他父亲"],
                "behavior_guidance": ["先问再答"],
                "dialogue_examples": ["「今天怎么样？」"],
            },
        },
        headers=_auth(),
    )
    assert response.status_code == 200, response.text

    written = await _genome_of(store, response.json()["companion"]["companion_id"])
    genome = written["genome"]
    assert genome["constitution"]["self_concept"] == "我是一个会记得你说过的话的伙伴"
    assert genome["constitution"]["values"] == ["诚实"]
    assert genome["constitution"]["boundaries"] == ["不替他做决定"]
    assert genome["character"]["portrait"] == "安静，话不多，但记得住"
    assert genome["relationship"]["narrative"] == "我们是从一次很长的深夜对话开始的"
    assert genome["relationship"]["safety_boundaries"] == ["不提他父亲"]
    assert genome["expression"]["voice_portrait"] == "短句，不用感叹号"
    assert genome["expression"]["behavior_guidance"] == ["先问再答"]
    assert genome["expression"]["dialogue_examples"] == ["「今天怎么样？」"]
    # Authored and defaulted are different facts, and the record says which.
    assert written["source"]["source_type"] == "owner_authored"
    assert genome["provenance"]["origin"] == "owner_authored"


async def test_a_retry_carrying_different_authoring_is_a_conflict(client) -> None:
    """A personality is not something to overwrite on a lost response.

    The request is fingerprinted whole, so authoring is inside the comparison
    that already protects the name. Without that, a retry from a phone whose
    form had changed would silently replace who somebody decided their Eidolon
    was — and the first answer would have said it worked.
    """

    http, store, _settings = client
    await _owner_with_one(store)
    first = await http.put(
        PATH.format(owner="owner-1", operation=OPERATION),
        json={"companion_display_name": "小南", "persona": {"self_concept": "我记得"}},
        headers=_auth(),
    )
    assert first.status_code == 200, first.text

    again = await http.put(
        PATH.format(owner="owner-1", operation=OPERATION),
        json={"companion_display_name": "小南", "persona": {"self_concept": "我不记得"}},
        headers=_auth(),
    )
    assert again.status_code == 409, again.text

    # And the same request twice is still the same Companion, not a second one.
    replay = await http.put(
        PATH.format(owner="owner-1", operation=OPERATION),
        json={"companion_display_name": "小南", "persona": {"self_concept": "我记得"}},
        headers=_auth(),
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["replayed"] is True


async def test_the_template_is_what_asking_for_nothing_would_have_written(
    client,
) -> None:
    """The read a form starts from, checked against the write it precedes.

    If these two ever disagree, the screen shows a personality this Host does
    not use, and the person edits a description of something else. Asserted
    across the two routes rather than inside one builder, because that is where
    the disagreement would actually live.
    """

    http, store, settings = client
    await _owner_with_one(store)

    # The template now lives on the persona authority — a read with no Owner in
    # it, about what a genome starts as, belongs beside the genome routes. This
    # test therefore spans two authorities, which is exactly where a drift
    # between "what the form shows" and "what provisioning writes" would live.
    persona_app = companion_authority_app(settings)
    async with (
        persona_app.router.lifespan_context(persona_app),
        httpx.AsyncClient(
            transport=ASGITransport(app=persona_app), base_url="http://persona.test"
        ) as persona_http,
    ):
        template = await persona_http.get(
            "/api/companion-authority/v1/persona-authoring-template",
            headers={"Authorization": f"Bearer {COMPANION_TOKEN}"},
        )
    assert template.status_code == 200, template.text

    # Send the template straight back, untouched.
    created = await http.put(
        PATH.format(owner="owner-1", operation=OPERATION),
        json={"companion_display_name": "小南", "persona": template.json()},
        headers=_auth(),
    )
    assert created.status_code == 200, created.text
    round_tripped = await _genome_of(
        store, created.json()["companion"]["companion_id"]
    )

    # ... and compare with what asking for nothing writes.
    default = await http.put(
        PATH.format(owner="owner-1", operation=OTHER_OPERATION),
        json={"companion_display_name": "小南"},
        headers=_auth(),
    )
    assert default.status_code == 200, default.text
    defaulted = await _genome_of(store, default.json()["companion"]["companion_id"])

    for section in ("constitution", "character", "relationship", "expression"):
        assert round_tripped["genome"][section] == defaulted["genome"][section], section


async def test_the_template_needs_the_authority_credential(tmp_path) -> None:
    """A product default is not a secret, but this plane has one rule."""

    settings = DataSettings(sqlite_path=str(tmp_path / "template.sqlite3"))
    writer = DataStore.open(settings)
    await writer.init_schema()
    await writer.close()
    app = companion_authority_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=ASGITransport(app=app), base_url="http://persona.test"
        ) as http,
    ):
        assert (
            await http.get(
                "/api/companion-authority/v1/persona-authoring-template"
            )
        ).status_code == 401


async def test_an_unknown_authoring_field_is_refused_rather_than_ignored(
    client,
) -> None:
    """Silently dropping a field is how a person loses a sentence they wrote."""

    http, store, _settings = client
    await _owner_with_one(store)
    response = await http.put(
        PATH.format(owner="owner-1", operation=OPERATION),
        json={
            "companion_display_name": "小南",
            "persona": {"favourite_colour": "青"},
        },
        headers=_auth(),
    )
    assert response.status_code == 422, response.text
