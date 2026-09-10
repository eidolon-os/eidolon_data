from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path

import httpx
import pytest
from eidolon_sdk.biz.system_data import CompanionRuntimeSnapshot
from jsonschema import Draft202012Validator
from sqlalchemy.exc import OperationalError

from eidolon_data import DataSettings, DataStore
from eidolon_data.api.companion_authority import create_app
from eidolon_data.api.workspace_authority import create_app as create_workspace_app

pytestmark = pytest.mark.integration
IDENTITY_SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "eidolon_data"
    / "contracts"
    / "schemas"
    / "companion"
    / "identity.schema.json"
)
RUNTIME_SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "eidolon_data"
    / "contracts"
    / "schemas"
    / "companion"
    / "runtime-snapshot.schema.json"
)
MEMORY_ROSTER_SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "eidolon_data"
    / "contracts"
    / "schemas"
    / "memory"
    / "runtime-roster.schema.json"
)
MEMORY_ROSTER_TOKEN = "memory-runtime-roster-token-0001"


async def _seed(path) -> DataSettings:
    settings = DataSettings(
        sqlite_path=str(path),
        object_store_path=str(Path(path).with_suffix(".objects")),
    )
    writer = DataStore.open(settings)
    await writer.init_schema()
    await writer.owner_commands.create_owner(owner_id="owner-1")
    await writer.companion_workspaces.provision_workspace(
        owner_id="owner-1",
        companion_id="companion-1",
        genome_id="genome-1",
        realm_id="realm-1",
        kind="conversational",
    )
    await writer.close()
    return settings


async def test_query_only_connection_reads_but_cannot_initialize_or_write(tmp_path) -> None:
    settings = await _seed(tmp_path / "readonly.sqlite3")
    reader = DataStore.open(settings.model_copy(update={"sqlite_read_only": True}))
    try:
        await reader.validate_schema()
        assert (await reader.companions.get("companion-1")).owner_id == "owner-1"
        with pytest.raises(RuntimeError, match="cannot initialize"):
            await reader.init_schema()
        with pytest.raises(OperationalError):
            await reader.owner_commands.create_owner(owner_id="write-must-fail")
    finally:
        await reader.close()


async def test_companion_authority_auth_and_exact_contract(tmp_path) -> None:
    settings = await _seed(tmp_path / "authority.sqlite3")
    token = "companion-authority-token-000001"
    app = create_app(
        settings,
        service_token=token,
        memory_roster_token=MEMORY_ROSTER_TOKEN,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://data.test",
        ) as client,
    ):
        assert (await client.get("/health")).json() == {"status": "ready"}
        path = "/api/companion-authority/v1/companions/companion-1"
        assert (await client.get(path)).status_code == 401
        assert (
            await client.get(path, headers={"Authorization": "Bearer wrong-token-value"})
        ).status_code == 403
        response = await client.get(path, headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        assert response.json() == {
            "operation": "companion.identity",
            "companion_id": "companion-1",
            "owner_id": "owner-1",
            # The name its Owner gave it, which this authority has stored all
            # along and did not answer with until now.
            "display_name": "owner-1 Companion",
            "lifecycle_state": "active",
            # The two axes the old ``role`` column hid: what kind of companion
            # this is, and the version a writer compares against.
            "kind": "conversational",
            "revision": 1,
        }
        Draft202012Validator(json.loads(IDENTITY_SCHEMA.read_text(encoding="utf-8"))).validate(
            response.json()
        )
        assert (
            await client.get(
                "/api/companion-authority/v1/companions/missing",
                headers={"Authorization": f"Bearer {token}"},
            )
        ).status_code == 404


async def test_memory_runtime_roster_has_distinct_auth_and_exact_contract(tmp_path) -> None:
    settings = await _seed(tmp_path / "memory-roster.sqlite3")
    companion_token = "companion-authority-token-000001"
    app = create_app(
        settings,
        service_token=companion_token,
        memory_roster_token=MEMORY_ROSTER_TOKEN,
    )
    path = "/api/companion-authority/v1/memory-runtime-roster"
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://data.test",
        ) as client,
    ):
        assert (await client.get(path)).status_code == 401
        assert (
            await client.get(
                path,
                headers={"Authorization": f"Bearer {companion_token}"},
            )
        ).status_code == 403
        response = await client.get(
            path,
            headers={"Authorization": f"Bearer {MEMORY_ROSTER_TOKEN}"},
        )
        assert response.status_code == 200
        assert response.json() == {
            "contract_version": "1",
            "operation": "memory.runtime-roster",
            "realms": [
                {
                    "realm_id": "realm-1",
                    "owner_id": "owner-1",
                    "engine": "mempalace",
                    "engine_config": {},
                }
            ],
        }
        Draft202012Validator(json.loads(MEMORY_ROSTER_SCHEMA.read_text(encoding="utf-8"))).validate(
            response.json()
        )
        assert (
            await client.get(
                "/api/companion-authority/v1/companions/companion-1",
                headers={"Authorization": f"Bearer {MEMORY_ROSTER_TOKEN}"},
            )
        ).status_code == 403


async def test_memory_runtime_roster_is_empty_for_fresh_or_archived_data(tmp_path) -> None:
    database = tmp_path / "empty-roster.sqlite3"
    settings = DataSettings(
        sqlite_path=str(database),
        object_store_path=str(tmp_path / "objects"),
    )
    store = DataStore.open(settings)
    await store.init_schema()
    await store.close()
    app = create_app(
        settings,
        service_token="companion-authority-token-000001",
        memory_roster_token=MEMORY_ROSTER_TOKEN,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://data.test",
        ) as client,
    ):
        response = await client.get(
            "/api/companion-authority/v1/memory-runtime-roster",
            headers={"Authorization": f"Bearer {MEMORY_ROSTER_TOKEN}"},
        )
        assert response.status_code == 200
        assert response.json()["realms"] == []


async def test_companion_authority_serves_runtime_snapshot_and_face(tmp_path) -> None:
    settings = await _seed(tmp_path / "runtime-authority.sqlite3")
    image = b"\xff\xd8runtime-face\xff\xd9"
    digest = hashlib.sha256(image).hexdigest()
    writer = DataStore.open(settings)
    try:
        await writer.owner_commands.create_owner(owner_id="owner-without-default")
        await writer.companion_workspaces.provision_workspace(
            owner_id="owner-1",
            companion_id="companion-2",
            genome_id="genome-2",
            realm_id="realm-2",
        )
        key = f"owner-1/companion-face/{digest}.jpg"
        writer.object_storage.put(key, image, expected_sha256=digest)
        await writer.companion_faces.set_face(
            companion_id="companion-1",
            cond_storage_key=key,
            cond_content_type="image/jpeg",
            cond_size_bytes=len(image),
            cond_sha256=digest,
        )
    finally:
        await writer.close()

    token = "companion-authority-token-000001"
    app = create_app(
        settings,
        service_token=token,
        memory_roster_token=MEMORY_ROSTER_TOKEN,
    )
    headers = {"Authorization": f"Bearer {token}"}
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://data.test",
        ) as client,
    ):
        runtime = await client.get(
            "/api/companion-authority/v1/companions/companion-1/runtime-snapshot",
            headers=headers,
        )
        assert runtime.status_code == 200
        body = runtime.json()
        assert body["owner_id"] == "owner-1"
        assert body["companion_id"] == "companion-1"
        assert body["memory_realm"]["realm_id"] == "realm-1"
        assert body["persona_genome"]["genome_id"] == "genome-1"
        assert body["persona_genome"]["genome"]["schema_version"] == "eidolon.persona_genome"
        Draft202012Validator(json.loads(RUNTIME_SCHEMA.read_text(encoding="utf-8"))).validate(body)
        assert CompanionRuntimeSnapshot.model_validate(body).companion_id == "companion-1"

        assert (
            await client.get(
                "/api/companion-authority/v1/companions/missing/runtime-snapshot",
                headers=headers,
            )
        ).status_code == 404
        assert (
            await client.get(
                "/api/companion-authority/v1/companions/companion-1/runtime-snapshot",
                params={"genome_id": "missing"},
                headers=headers,
            )
        ).status_code == 404
        assert (
            await client.get(
                "/api/companion-authority/v1/companions/companion-1/runtime-snapshot",
                params={"genome_id": "genome-2"},
                headers=headers,
            )
        ).status_code == 412

        owner_runtime = await client.get(
            "/api/companion-authority/v1/owners/owner-1/default-runtime-snapshot",
            headers=headers,
        )
        assert owner_runtime.json() == body
        assert (
            await client.get(
                "/api/companion-authority/v1/owners/missing/default-runtime-snapshot",
                headers=headers,
            )
        ).status_code == 404
        assert (
            await client.get(
                "/api/companion-authority/v1/owners/owner-without-default/default-runtime-snapshot",
                headers=headers,
            )
        ).status_code == 412

        face = await client.get(
            "/api/companion-authority/v1/companions/companion-1/face",
            headers=headers,
        )
        assert face.status_code == 200
        assert face.headers["content-type"] == "image/jpeg"
        assert face.headers["etag"] == f'"sha256:{digest}"'
        assert face.content == image
        assert (
            await client.get(
                "/api/companion-authority/v1/companions/companion-2/face",
                headers=headers,
            )
        ).status_code == 204

        missing_face = await client.get(
            "/api/companion-authority/v1/companions/missing/face",
            headers=headers,
        )
        assert missing_face.status_code == 404

        stored_face = Path(settings.object_store_path) / key
        stored_face.unlink()
        unavailable_face = await client.get(
            "/api/companion-authority/v1/companions/companion-1/face",
            headers=headers,
        )
        assert unavailable_face.status_code == 500
        assert unavailable_face.json()["detail"] == "companion face is unavailable"

        stored_face.write_bytes(b"tampered")
        corrupt_face = await client.get(
            "/api/companion-authority/v1/companions/companion-1/face",
            headers=headers,
        )
        assert corrupt_face.status_code == 500
        assert corrupt_face.json()["detail"] == "companion face integrity check failed"


async def test_runtime_authority_fails_closed_for_archived_workspace(tmp_path) -> None:
    settings = await _seed(tmp_path / "archived-runtime-authority.sqlite3")
    writer = DataStore.open(settings)
    try:
        await writer.owner_commands.archive_owner("owner-1")
    finally:
        await writer.close()

    token = "companion-authority-token-000001"
    app = create_app(
        settings,
        service_token=token,
        memory_roster_token=MEMORY_ROSTER_TOKEN,
    )
    headers = {"Authorization": f"Bearer {token}"}
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://data.test",
        ) as client,
    ):
        for path in (
            "/api/companion-authority/v1/companions/companion-1/runtime-snapshot",
            "/api/companion-authority/v1/owners/owner-1/default-runtime-snapshot",
        ):
            assert (await client.get(path, headers=headers)).status_code == 412

        # The face is not in that set, and deliberately so: being readable is
        # not being runnable. Nothing can start a session here — the two routes
        # above refuse — and the one caller left is a management screen showing
        # a person what their own Eidolon looks like. Refusing that would hide
        # an archived Companion's portrait from its Owner, which is not what
        # "put away" was ever supposed to mean.
        face = await client.get(
            "/api/companion-authority/v1/companions/companion-1/face",
            headers=headers,
        )
        assert face.status_code in {200, 204}


@pytest.mark.parametrize(
    ("statement", "expected_status", "expected_detail"),
    [
        (
            "DELETE FROM owners WHERE owner_id = 'owner-1'",
            409,
            "companion owner is missing",
        ),
        (
            "UPDATE companions SET default_memory_realm_id = NULL "
            "WHERE companion_id = 'companion-1'",
            412,
            "companion has no default memory realm",
        ),
        (
            "DELETE FROM memory_realms WHERE realm_id = 'realm-1'",
            409,
            "default memory realm is missing",
        ),
        (
            "UPDATE memory_realms SET status = 'inactive' WHERE realm_id = 'realm-1'",
            412,
            "default memory realm is not active in scope",
        ),
        (
            "UPDATE companions SET current_genome_id = NULL WHERE companion_id = 'companion-1'",
            412,
            "companion has no current persona genome",
        ),
    ],
)
async def test_runtime_authority_fails_closed_for_corrupt_references(
    tmp_path,
    statement: str,
    expected_status: int,
    expected_detail: str,
) -> None:
    database_path = tmp_path / "corrupt-runtime-authority.sqlite3"
    settings = await _seed(database_path)
    with closing(sqlite3.connect(database_path)) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(statement)
        connection.commit()

    token = "companion-authority-token-000001"
    app = create_app(
        settings,
        service_token=token,
        memory_roster_token=MEMORY_ROSTER_TOKEN,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://data.test",
        ) as client,
    ):
        response = await client.get(
            "/api/companion-authority/v1/companions/companion-1/runtime-snapshot",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == expected_status
        assert response.json()["detail"] == expected_detail


@pytest.mark.parametrize("token", ["", "short", " " * 30])
def test_companion_authority_rejects_weak_service_tokens(token: str) -> None:
    with pytest.raises(RuntimeError, match="at least 24"):
        create_app(
            DataSettings(sqlite_path=":memory:"),
            service_token=token,
            memory_roster_token=MEMORY_ROSTER_TOKEN,
        )


@pytest.mark.parametrize("token", ["", "short", " " * 30])
def test_companion_authority_rejects_weak_memory_roster_tokens(token: str) -> None:
    with pytest.raises(RuntimeError, match="EIDOLON_DATA_MEMORY_RUNTIME_ROSTER_TOKEN"):
        create_app(
            DataSettings(sqlite_path=":memory:"),
            service_token="companion-authority-token-000001",
            memory_roster_token=token,
        )


@pytest.mark.asyncio
async def _companion_authority(tmp_path):
    settings = await _seed(tmp_path / "authority.sqlite3")
    token = "companion-authority-token-000001"
    app = create_app(
        settings,
        service_token=token,
        memory_roster_token=MEMORY_ROSTER_TOKEN,
    )
    return app, token


@pytest.mark.asyncio
async def test_an_owner_may_name_their_companion(tmp_path) -> None:
    """The one thing about a Companion its Owner sets directly.

    The name was written at onboarding and never read back, so the product
    showed c_683f9… where a person had said what to call it. Naming it is
    therefore both the read and the write this authority was missing.
    """

    app, token = await _companion_authority(tmp_path)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://data.test"
        ) as client,
    ):
        headers = {"Authorization": f"Bearer {token}"}
        path = "/api/companion-authority/v1/companions/companion-1"

        snapshot = (await client.get(f"{path}/persona", headers=headers)).json()
        renamed = await client.put(
            f"{path}/persona",
            json=_persona_action(snapshot, "rename", display_name="小忆"),
            headers=headers,
        )

        assert renamed.status_code == 200
        assert renamed.json()["display_name"] == "小忆"
        # It is the authority's answer that changed, not just this reply.
        assert (await client.get(path, headers=headers)).json()["display_name"] == "小忆"


@pytest.mark.asyncio
async def test_naming_refuses_what_it_cannot_carry_out(tmp_path) -> None:
    app, token = await _companion_authority(tmp_path)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://data.test"
        ) as client,
    ):
        headers = {"Authorization": f"Bearer {token}"}

        path = "/api/companion-authority/v1/companions/companion-1/persona"
        snapshot = (await client.get(path, headers=headers)).json()
        blank = await client.put(
            path, json=_persona_action(snapshot, "rename", display_name="   "), headers=headers
        )
        missing = await client.put(
            path.replace("companion-1", "nobody"),
            json=_persona_action(snapshot, "rename", display_name="小忆"),
            headers=headers,
        )
        unauthorized = await client.put(
            path, json=_persona_action(snapshot, "rename", display_name="小忆")
        )

        # A name of only spaces would erase the one the Owner has.
        assert blank.status_code == 422
        # Answering "which Companion?" rather than reporting a rename of nothing.
        assert missing.status_code == 404
        assert unauthorized.status_code == 401


@pytest.mark.asyncio
async def test_going_back_is_a_new_chapter_not_an_undo(tmp_path) -> None:
    """A history you can rewrite is not a history.

    Returning a Companion to what it was appends a version carrying that older
    content, so the months in between stay on the record — and so does the act
    of going back, which is the part someone will want to find later when they
    wonder what happened.
    """

    settings = await _seed(tmp_path / "authority.sqlite3")
    # A Companion that has been two things, because "going back" means nothing
    # for one that has only ever been itself — and a test that passes because
    # there was nowhere to go is a test that proves nothing.
    writer = DataStore.open(settings)
    await writer.persona_commands.create_genome(
        genome_id="genome-2",
        companion_id="companion-1",
        owner_id="owner-1",
        event_id="audit-genome-2",
        version=2,
        base_genome_id="genome-1",
    )
    await writer.close()
    token = "companion-authority-token-000001"
    app = create_app(settings, service_token=token, memory_roster_token=MEMORY_ROSTER_TOKEN)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://data.test"
        ) as client,
    ):
        headers = {"Authorization": f"Bearer {token}"}
        base = "/api/companion-authority/v1/companions/companion-1"
        before = (await client.get(f"{base}/persona-timeline", headers=headers)).json()
        original = before["chapters"][-1]

        # Nothing to go back to yet: it is already what it was.
        current = before["chapters"][0]
        snapshot = (await client.get(f"{base}/persona", headers=headers)).json()
        already = await client.put(
            f"{base}/persona",
            json=_persona_action(snapshot, "restore", restore_genome_id=current["genome_id"]),
            headers=headers,
        )

        assert already.status_code == 200
        assert already.json()["genome_id"] == current["genome_id"]
        assert current["is_current"] is True
        assert original["is_current"] is False

        # And the path that actually goes back. Until this test existed the only
        # coverage of this route was the refusal above, and the success path
        # raised a foreign-key error on every call — a restore button that could
        # only ever 500, projected all the way to a phone.
        moved_on = await client.put(
            f"{base}/persona",
            json=_persona_action(
                already.json(),
                "restore",
                restore_genome_id=original["genome_id"],
                operation_id="restore-old",
            ),
            headers=headers,
        )

        assert moved_on.status_code == 200, moved_on.text
        # Appended, not rewound: a new chapter carrying the old content, so the
        # record keeps what happened in between and says when someone went back.
        assert moved_on.json()["genome_id"] not in {original["genome_id"], "genome-2"}
        after = (await client.get(f"{base}/persona-timeline", headers=headers)).json()
        assert [chapter["version"] for chapter in after["chapters"]] == [3, 2, 1]
        assert after["chapters"][0]["restored_from_version"] == 1


@pytest.mark.asyncio
async def test_the_timeline_says_when_and_why_rather_than_what_hash(tmp_path) -> None:
    app, token = await _companion_authority(tmp_path)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://data.test"
        ) as client,
    ):
        headers = {"Authorization": f"Bearer {token}"}
        timeline = await client.get(
            "/api/companion-authority/v1/companions/companion-1/persona-timeline",
            headers=headers,
        )

        assert timeline.status_code == 200
        chapter = timeline.json()["chapters"][0]
        # What a person reads: when it changed, and what changed in its own
        # words. The genome hash is deliberately not part of this answer.
        assert set(chapter) == {
            "genome_id",
            "version",
            "lifecycle_state",
            "change_summary",
            "restored_from_version",
            "is_current",
            "created_at",
        }
        assert (
            await client.get(
                "/api/companion-authority/v1/companions/nobody/persona-timeline",
                headers=headers,
            )
        ).status_code == 404


async def test_an_owner_gives_their_eidolon_a_face_and_takes_it_back(tmp_path) -> None:
    """The face is set, replaced and cleared over the same one authority."""

    settings = await _seed(tmp_path / "face-write-authority.sqlite3")
    token = "companion-authority-token-000002"
    app = create_app(
        settings,
        service_token=token,
        memory_roster_token=MEMORY_ROSTER_TOKEN,
    )
    headers = {"Authorization": f"Bearer {token}"}
    jpeg = {"Content-Type": "image/jpeg", **headers}
    first = b"\xff\xd8\xff first face \xff\xd9"
    second = b"\xff\xd8\xff second face \xff\xd9"
    path = "/api/companion-authority/v1/companions/companion-1/face"

    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://data.test",
        ) as client,
    ):
        assert (await client.put(path, content=first)).status_code == 401

        blank = await client.get(f"{path}-state", headers=headers)
        assert blank.status_code == 200
        assert blank.json()["has_face"] is False

        stored = await client.put(path, content=first, headers=jpeg)
        assert stored.status_code == 200
        assert stored.json()["has_face"] is True
        assert stored.json()["sha256"] == hashlib.sha256(first).hexdigest()

        served = await client.get(path, headers=headers)
        assert served.status_code == 200
        assert served.content == first
        assert served.headers["content-type"] == "image/jpeg"

        # A new face supersedes the old one: what it looked like is part of
        # what it has been, so the row is added rather than overwritten.
        replaced = await client.put(path, content=second, headers=jpeg)
        assert replaced.status_code == 200
        assert replaced.json()["face_asset_id"] != stored.json()["face_asset_id"]
        assert (await client.get(path, headers=headers)).content == second

        # Not a JPEG, and refused where the person can still choose another.
        assert (await client.put(path, content=b"GIF89a", headers=jpeg)).status_code == 415
        assert (await client.put(path, content=second, headers=headers)).status_code == 415
        assert (await client.put(path, content=b"", headers=jpeg)).status_code == 422
        # The refusals changed nothing.
        assert (await client.get(path, headers=headers)).content == second

        cleared = await client.delete(path, headers=headers)
        assert cleared.status_code == 200
        assert cleared.json()["has_face"] is False
        assert (await client.get(path, headers=headers)).status_code == 204
        # Clearing a face that is already gone is not an error: the Owner
        # asked for no face, and there is no face.
        assert (await client.delete(path, headers=headers)).status_code == 200

        assert (
            await client.put(
                "/api/companion-authority/v1/companions/missing/face",
                content=first,
                headers=jpeg,
            )
        ).status_code == 404


@pytest.mark.asyncio
async def test_a_companion_can_be_put_away_and_brought_back_over_http(tmp_path) -> None:
    """One route for the three moves, because the body states a desired end.

    Which also makes a retry after a lost answer safe — the thing a phone does
    constantly — and lets the authority decide whether the state asked for is
    reachable from where the Companion is, rather than a caller deciding by
    picking a verb.
    """

    settings = await _seed(tmp_path / "lifecycle.sqlite3")
    writer = DataStore.open(settings)
    await writer.companion_workspaces.provision_workspace(
        owner_id="owner-1",
        companion_id="companion-2",
        genome_id="genome-2",
        kind="conversational",
    )
    await writer.companion_workspaces.set_default_companion(
        owner_id="owner-1", companion_id="companion-1"
    )
    await writer.close()
    token = "workspace-authority-token-0001"
    app = create_workspace_app(settings, service_token=token)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://data.test"
        ) as client,
    ):
        headers = {"Authorization": f"Bearer {token}"}
        lifecycle = "/api/workspace-authority/v1/companions/companion-1/lifecycle"

        # Retiring the Companion that answers for this Owner needs a successor
        # named in the same request.
        without = await client.put(
            lifecycle,
            json={"owner_id": "owner-1", "lifecycle_state": "retiring"},
            headers=headers,
        )
        assert without.status_code == 409
        assert without.json()["detail"]["code"] == "default_replacement_required"

        retired = await client.put(
            lifecycle,
            json={
                "owner_id": "owner-1",
                "lifecycle_state": "retiring",
                "replacement_companion_id": "companion-2",
            },
            headers=headers,
        )
        assert retired.status_code == 200
        assert retired.json()["lifecycle_state"] == "retiring"
        # The answer carries who answers now: the caller that just retired a
        # default needs it, and asking again would re-read what this transaction
        # already settled.
        assert retired.json()["default_companion_id"] == "companion-2"

        archived = await client.put(
            lifecycle,
            json={"owner_id": "owner-1", "lifecycle_state": "archived"},
            headers=headers,
        )
        assert archived.status_code == 200

        # Sending it again is the lost-response retry, and it succeeds.
        again = await client.put(
            lifecycle,
            json={"owner_id": "owner-1", "lifecycle_state": "archived"},
            headers=headers,
        )
        assert again.status_code == 200
        assert again.json()["revision"] == archived.json()["revision"]

        restored = await client.put(
            lifecycle,
            json={"owner_id": "owner-1", "lifecycle_state": "active"},
            headers=headers,
        )
        assert restored.status_code == 200
        # Restoring does not take the role back.
        assert restored.json()["default_companion_id"] == "companion-2"


@pytest.mark.asyncio
async def test_another_owners_companion_cannot_be_archived_over_http(tmp_path) -> None:
    """404, the same answer a Companion that does not exist gets."""

    settings = await _seed(tmp_path / "lifecycle-scope.sqlite3")
    writer = DataStore.open(settings)
    await writer.owner_commands.create_owner(owner_id="owner-2")
    await writer.close()
    token = "workspace-authority-token-0001"
    app = create_workspace_app(settings, service_token=token)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://data.test"
        ) as client,
    ):
        headers = {"Authorization": f"Bearer {token}"}
        theirs = await client.put(
            "/api/workspace-authority/v1/companions/companion-1/lifecycle",
            json={"owner_id": "owner-2", "lifecycle_state": "retiring"},
            headers=headers,
        )
        absent = await client.put(
            "/api/workspace-authority/v1/companions/companion-nowhere/lifecycle",
            json={"owner_id": "owner-2", "lifecycle_state": "retiring"},
            headers=headers,
        )

        assert theirs.status_code == 404
        assert absent.status_code == 404
        assert theirs.json()["detail"] == absent.json()["detail"]


@pytest.mark.asyncio
async def test_an_archived_companion_will_not_hand_out_a_runtime_snapshot(tmp_path) -> None:
    """What "put away" has to mean, one layer down.

    The lifecycle route changes a row; this is the place that row has to be
    obeyed. Asking for a runtime snapshot is how a session begins, so a
    Companion that is not active must not produce one — otherwise archiving is
    bookkeeping and a device that already knows the id keeps talking to
    something its owner retired.

    412 rather than 404: it is there, and it is not answering. A caller can tell
    that apart from an id that names nothing, and only one of the two is worth
    showing a person as "you put this one away".
    """

    settings = await _seed(tmp_path / "archived-runtime.sqlite3")
    writer = DataStore.open(settings)
    await writer.companion_workspaces.provision_workspace(
        owner_id="owner-1", companion_id="companion-2", genome_id="genome-2"
    )
    await writer.companion_workspaces.set_default_companion(
        owner_id="owner-1", companion_id="companion-2"
    )
    await writer.close()

    workspace_token = "workspace-authority-token-0001"
    runtime_token = "companion-authority-token-000001"
    workspace = create_workspace_app(settings, service_token=workspace_token)
    runtime = create_app(
        settings, service_token=runtime_token, memory_roster_token=MEMORY_ROSTER_TOKEN
    )
    async with (
        workspace.router.lifespan_context(workspace),
        runtime.router.lifespan_context(runtime),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=workspace), base_url="http://workspace.test"
        ) as workspace_client,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=runtime), base_url="http://runtime.test"
        ) as runtime_client,
    ):
        snapshot = "/api/companion-authority/v1/companions/companion-1/runtime-snapshot"
        lifecycle = "/api/workspace-authority/v1/companions/companion-1/lifecycle"
        runtime_headers = {"Authorization": f"Bearer {runtime_token}"}
        workspace_headers = {"Authorization": f"Bearer {workspace_token}"}

        assert (await runtime_client.get(snapshot, headers=runtime_headers)).status_code == 200

        for state in ("retiring", "archived"):
            moved = await workspace_client.put(
                lifecycle,
                json={"owner_id": "owner-1", "lifecycle_state": state},
                headers=workspace_headers,
            )
            assert moved.status_code == 200
            refused = await runtime_client.get(snapshot, headers=runtime_headers)
            assert refused.status_code == 412, state

        brought_back = await workspace_client.put(
            lifecycle,
            json={"owner_id": "owner-1", "lifecycle_state": "active"},
            headers=workspace_headers,
        )
        assert brought_back.status_code == 200
        assert (await runtime_client.get(snapshot, headers=runtime_headers)).status_code == 200


@pytest.mark.asyncio
async def test_what_happened_to_this_owners_things_is_readable_newest_first(
    tmp_path,
) -> None:
    """The governance facts this authority already writes, read back.

    They are written in the same transaction as the change itself, which is what
    makes them a record rather than a log: an event exists exactly when the
    thing it describes happened. Nothing had ever read them — the outbox has a
    dispatcher-shaped hole where a reader should be, and a person had no way to
    see what had been done to their own Eidolons.
    """

    settings = await _seed(tmp_path / "governance-events.sqlite3")
    writer = DataStore.open(settings)
    await writer.companion_workspaces.provision_workspace(
        owner_id="owner-1", companion_id="companion-2", genome_id="genome-2"
    )
    await writer.companion_workspaces.set_default_companion(
        owner_id="owner-1", companion_id="companion-2"
    )
    await writer.companion_workspaces.put_away_companion(
        owner_id="owner-1", companion_id="companion-1"
    )
    await writer.owner_commands.create_owner(owner_id="owner-2")
    await writer.close()

    token = "workspace-authority-token-0001"
    app = create_workspace_app(settings, service_token=token)
    path = "/api/workspace-authority/v1/owners/owner-1/governance-events"
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://data.test"
        ) as client,
    ):
        headers = {"Authorization": f"Bearer {token}"}
        assert (await client.get(path)).status_code == 401

        answered = await client.get(path, headers=headers)
        assert answered.status_code == 200
        body = answered.json()
        actions = [event["action"] for event in body["events"]]
        # Newest first, and the archive is two facts because it was two moves in
        # one transaction — the record says the retirement happened, because it
        # did.
        assert actions[:2] == ["companion.archived", "companion.retirement_begun"]
        # Everything that made this Owner what it is, in one record: two
        # Companions arriving, the pointer moving, and the one being put away.
        assert "companion.workspace.initialized" in actions
        assert "owner.default_companion_changed" in actions
        # Both sides of the change: an event that says the pointer moved without
        # saying what it moved to is one nobody can read.
        moved = next(
            event
            for event in body["events"]
            if event["action"] == "owner.default_companion_changed"
        )
        assert moved["payload"]["companion_id"] == "companion-2"
        assert moved["payload"]["previous_companion_id"] == "companion-1"
        assert actions[-1] == "owner.created"
        assert body["events"][0]["subject_id"] == "companion-1"

        # One page at a time, and the cursor walks backwards through it.
        first = await client.get(path, params={"limit": 2}, headers=headers)
        assert len(first.json()["events"]) == 2
        cursor = first.json()["next_cursor"]
        assert cursor is not None
        older = await client.get(path, params={"limit": 2, "before": cursor}, headers=headers)
        assert older.status_code == 200
        assert {event["event_id"] for event in older.json()["events"]}.isdisjoint(
            {event["event_id"] for event in first.json()["events"]}
        )

        # Another Owner's history is not this Owner's, and an Owner that does
        # not exist is absent rather than empty: "nothing happened" and "there
        # is nobody here" are different answers.
        theirs = await client.get(
            "/api/workspace-authority/v1/owners/owner-2/governance-events",
            headers=headers,
        )
        assert theirs.status_code == 200
        assert [event["action"] for event in theirs.json()["events"]] == ["owner.created"]
        assert all(event["subject_id"] == "owner-2" for event in theirs.json()["events"])
        nobody = await client.get(
            "/api/workspace-authority/v1/owners/owner-nowhere/governance-events",
            headers=headers,
        )
        assert nobody.status_code == 404


@pytest.mark.asyncio
async def test_changing_your_mind_is_a_new_chapter_too(tmp_path) -> None:
    """Editing and going back are one act pointing two ways.

    Both append; neither edits. That is what keeps "what this Companion has
    been" a record rather than a current value with a history-shaped name — and
    it means there is exactly one way a persona ever changes, however it was
    asked for.
    """

    settings = await _seed(tmp_path / "authority.sqlite3")
    token = "companion-authority-token-000001"
    app = create_app(settings, service_token=token, memory_roster_token=MEMORY_ROSTER_TOKEN)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://data.test"
        ) as client,
    ):
        headers = {"Authorization": f"Bearer {token}"}
        base = "/api/companion-authority/v1/companions/companion-1"

        before = (await client.get(f"{base}/persona", headers=headers)).json()
        assert "name" not in before, "the name is the Companion's, said once"

        written = {
            **before["persona"],
            "self_concept": "我是一个会记得你说过的话的伙伴",
            "values": ["诚实", "守时"],
        }
        answered = await client.put(
            f"{base}/persona",
            headers=headers,
            json=_edit_request(before, written),
        )
        assert answered.status_code == 200, answered.text
        assert answered.json()["genome_id"] != before["genome_id"]

        # It reads back as what was written...
        now = (await client.get(f"{base}/persona", headers=headers)).json()["persona"]
        assert now["self_concept"] == "我是一个会记得你说过的话的伙伴"
        assert now["values"] == ["诚实", "守时"]
        # ... and everything untouched came through unharmed.
        assert now["character_portrait"] == before["persona"]["character_portrait"]
        assert now["behavior_guidance"] == before["persona"]["behavior_guidance"]

        # ... as a chapter, with what it was still on the record.
        timeline = (await client.get(f"{base}/persona-timeline", headers=headers)).json()
        assert len(timeline["chapters"]) == 2
        assert timeline["chapters"][0]["is_current"] is True


@pytest.mark.asyncio
async def test_saving_without_changing_anything_writes_no_chapter(tmp_path) -> None:
    """Opening the screen and pressing save is not something that happened.

    The genome is content-addressed, so an untouched form hashes to what is
    already current and this is cheap to get right. Getting it wrong would pad
    the record with non-events, and a history of non-events is one nobody reads
    — which costs exactly the thing the history exists for.
    """

    settings = await _seed(tmp_path / "authority.sqlite3")
    token = "companion-authority-token-000001"
    app = create_app(settings, service_token=token, memory_roster_token=MEMORY_ROSTER_TOKEN)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://data.test"
        ) as client,
    ):
        headers = {"Authorization": f"Bearer {token}"}
        base = "/api/companion-authority/v1/companions/companion-1"

        unchanged = (await client.get(f"{base}/persona", headers=headers)).json()
        for _ in range(2):
            answered = await client.put(
                f"{base}/persona",
                headers=headers,
                json=_edit_request(unchanged, unchanged["persona"]),
            )
            assert answered.status_code == 200, answered.text

        timeline = (await client.get(f"{base}/persona-timeline", headers=headers)).json()
        assert len(timeline["chapters"]) == 1


@pytest.mark.asyncio
async def test_the_same_edit_sent_twice_writes_one_chapter(tmp_path) -> None:
    """A lost answer must not cost a chapter.

    The phone retries; the second request is byte-identical; the second genome
    would hash the same as the one just written. Without that, every dropped
    response would leave a duplicate entry in the record of who somebody's
    Eidolon has been.
    """

    settings = await _seed(tmp_path / "authority.sqlite3")
    token = "companion-authority-token-000001"
    app = create_app(settings, service_token=token, memory_roster_token=MEMORY_ROSTER_TOKEN)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://data.test"
        ) as client,
    ):
        headers = {"Authorization": f"Bearer {token}"}
        base = "/api/companion-authority/v1/companions/companion-1"
        snapshot = (await client.get(f"{base}/persona", headers=headers)).json()
        written = {
            **snapshot["persona"],
            "self_concept": "我记得",
        }

        first = await client.put(
            f"{base}/persona", headers=headers, json=_edit_request(snapshot, written)
        )
        again = await client.put(
            f"{base}/persona", headers=headers, json=_edit_request(snapshot, written)
        )

        assert first.status_code == 200 and again.status_code == 200
        assert first.json()["genome_id"] == again.json()["genome_id"]
        timeline = (await client.get(f"{base}/persona-timeline", headers=headers)).json()
        assert len(timeline["chapters"]) == 2, "one for the edit, one it was"


@pytest.mark.asyncio
async def test_an_edit_does_not_rename_the_eidolon(tmp_path) -> None:
    """Who it is and what it is called are two decisions.

    The name inside the genome is carried over rather than re-derived, so an
    edit cannot rewrite it as a side effect — and if the name and the Companion
    row have drifted apart, that stays renaming's problem instead of being
    silently papered over by whoever edits next.
    """

    settings = await _seed(tmp_path / "authority.sqlite3")
    token = "companion-authority-token-000001"
    app = create_app(settings, service_token=token, memory_roster_token=MEMORY_ROSTER_TOKEN)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://data.test"
        ) as client,
    ):
        headers = {"Authorization": f"Bearer {token}"}
        base = "/api/companion-authority/v1/companions/companion-1"
        snapshot = await client.get(f"{base}/runtime-snapshot", headers=headers)
        before = snapshot.json()["persona_genome"]["genome"]["constitution"]["name"]

        snapshot = (await client.get(f"{base}/persona", headers=headers)).json()
        written = {
            **snapshot["persona"],
            "self_concept": "我记得",
        }
        answer = await client.put(
            f"{base}/persona", headers=headers, json=_edit_request(snapshot, written)
        )
        assert answer.status_code == 200, answer.text

        after = (await client.get(f"{base}/runtime-snapshot", headers=headers)).json()
        assert after["persona_genome"]["genome"]["constitution"]["name"] == before


@pytest.mark.asyncio
async def test_authoring_needs_the_authority_credential(tmp_path) -> None:
    settings = await _seed(tmp_path / "authority.sqlite3")
    app = create_app(
        settings,
        service_token="companion-authority-token-000001",
        memory_roster_token=MEMORY_ROSTER_TOKEN,
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://data.test"
        ) as client,
    ):
        base = "/api/companion-authority/v1/companions/companion-1"
        assert (await client.get(f"{base}/persona")).status_code == 401
        assert (await client.put(f"{base}/persona", json={"persona": {}})).status_code == 401


def _edit_request(snapshot, persona, operation_id="test-edit"):
    return {
        "expected_base_genome_id": snapshot["genome_id"],
        "expected_preference_revision": snapshot["preference_revision"],
        "operation_id": operation_id,
        "persona": persona,
    }


def _persona_action(snapshot, action, **fields):
    return {
        "expected_base_genome_id": snapshot["genome_id"],
        "expected_preference_revision": snapshot["preference_revision"],
        "operation_id": f"test-{action}",
        "persona": {},
        "action": action,
        **fields,
    }
