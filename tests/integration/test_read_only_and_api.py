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
        role="primary",
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
                    "companion_id": "companion-1",
                    "engine": "mempalace",
                    "engine_config": {},
                }
            ],
        }
        Draft202012Validator(
            json.loads(MEMORY_ROSTER_SCHEMA.read_text(encoding="utf-8"))
        ).validate(response.json())
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
        await writer.owner_commands.create_owner(owner_id="owner-without-primary")
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
            "/api/companion-authority/v1/owners/owner-1/primary-runtime-snapshot",
            headers=headers,
        )
        assert owner_runtime.json() == body
        assert (
            await client.get(
                "/api/companion-authority/v1/owners/missing/primary-runtime-snapshot",
                headers=headers,
            )
        ).status_code == 404
        assert (
            await client.get(
                "/api/companion-authority/v1/owners/owner-without-primary/primary-runtime-snapshot",
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
            "/api/companion-authority/v1/owners/owner-1/primary-runtime-snapshot",
            "/api/companion-authority/v1/companions/companion-1/face",
        ):
            assert (await client.get(path, headers=headers)).status_code == 412


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

        renamed = await client.patch(path, json={"display_name": "小忆"}, headers=headers)

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

        blank = await client.patch(
            "/api/companion-authority/v1/companions/companion-1",
            json={"display_name": "   "},
            headers=headers,
        )
        missing = await client.patch(
            "/api/companion-authority/v1/companions/nobody",
            json={"display_name": "小忆"},
            headers=headers,
        )
        unauthorized = await client.patch(
            "/api/companion-authority/v1/companions/companion-1",
            json={"display_name": "小忆"},
        )

        # A name of only spaces would erase the one the Owner has.
        assert blank.status_code == 422
        # Answering "which Companion?" rather than reporting a rename of nothing.
        assert missing.status_code == 404
        assert unauthorized.status_code == 401
