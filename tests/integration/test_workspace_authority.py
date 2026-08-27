from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator

from eidolon_data import DataSettings, DataStore
from eidolon_data.api.workspace_authority import create_app

pytestmark = pytest.mark.integration
OPERATION_SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "eidolon_data"
    / "contracts"
    / "schemas"
    / "workspace"
    / "onboarding-operation.schema.json"
)


async def test_workspace_authority_auth_idempotency_and_query(tmp_path) -> None:
    settings = DataSettings(sqlite_path=str(tmp_path / "workspace-authority.sqlite3"))
    store = DataStore.open(settings)
    await store.init_schema()
    await store.close()
    token = "workspace-authority-token-000001"
    operation_id = "f8b886ff-d2e5-4d73-bb10-53d4a43f319e"
    path = f"/api/workspace-authority/v1/operations/{operation_id}"
    payload = {
        "owner_display_name": "Manson",
        "companion_display_name": "Eidolon",
    }
    app = create_app(settings, service_token=token)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://data.test",
        ) as client,
    ):
        assert (await client.get("/health")).json() == {"status": "ready"}
        assert (await client.put(path, json=payload)).status_code == 401
        assert (
            await client.put(
                path,
                json=payload,
                headers={"Authorization": "Bearer wrong-token-value"},
            )
        ).status_code == 403
        headers = {"Authorization": f"Bearer {token}"}
        first = await client.put(path, json=payload, headers=headers)
        assert first.status_code == 200
        body = first.json()
        assert body["operation_id"] == operation_id
        assert body["status"] == "succeeded"
        assert body["workspace"]["state"] == "ready"
        assert body["owner"]["owner_id"] == "owner_f8b886ffd2e54d73bb1053d4a43f319e"
        Draft202012Validator(json.loads(OPERATION_SCHEMA.read_text(encoding="utf-8"))).validate(
            body
        )

        replay = await client.put(path, json=payload, headers=headers)
        queried = await client.get(path, headers=headers)
        assert replay.status_code == 200
        assert replay.json() == body
        assert queried.status_code == 200
        assert queried.json() == body

        conflict = await client.put(
            path,
            json={**payload, "companion_display_name": "Different"},
            headers=headers,
        )
        assert conflict.status_code == 409
        assert "already in use" in conflict.json()["detail"]


async def test_workspace_authority_rejects_unknown_or_invalid_operations(tmp_path) -> None:
    settings = DataSettings(sqlite_path=str(tmp_path / "workspace-authority-errors.sqlite3"))
    store = DataStore.open(settings)
    await store.init_schema()
    await store.close()
    token = "workspace-authority-token-000002"
    headers = {"Authorization": f"Bearer {token}"}
    app = create_app(settings, service_token=token)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://data.test",
        ) as client,
    ):
        missing = await client.get(
            "/api/workspace-authority/v1/operations/7cab9151-c46a-4c90-a523-7e140ce49225",
            headers=headers,
        )
        invalid = await client.put(
            "/api/workspace-authority/v1/operations/not-a-uuid",
            headers=headers,
            json={"owner_display_name": "Owner"},
        )
        extra = await client.put(
            "/api/workspace-authority/v1/operations/7cab9151-c46a-4c90-a523-7e140ce49225",
            headers=headers,
            json={"owner_display_name": "Owner", "owner_id": "client-chosen"},
        )
        assert missing.status_code == 404
        assert invalid.status_code == 422
        assert extra.status_code == 422


async def test_owner_name_is_readable_and_correctable(tmp_path) -> None:
    """The name a person gives at first use is theirs to fix later."""

    settings = DataSettings(sqlite_path=str(tmp_path / "owner-rename.sqlite3"))
    store = DataStore.open(settings)
    await store.init_schema()
    await store.close()
    token = "workspace-authority-token-000002"
    operation_id = "0f1d0a5c-2f2e-4a1e-9a4a-1a2b3c4d5e6f"
    headers = {"Authorization": f"Bearer {token}"}
    app = create_app(settings, service_token=token)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://data.test",
        ) as client,
    ):
        created = await client.put(
            f"/api/workspace-authority/v1/operations/{operation_id}",
            json={"owner_display_name": "Manson", "companion_display_name": "小忆"},
            headers=headers,
        )
        assert created.status_code == 200
        owner_id = created.json()["owner"]["owner_id"]
        path = f"/api/workspace-authority/v1/owners/{owner_id}"

        assert (await client.get(path)).status_code == 401
        assert (await client.patch(path, json={"display_name": "x"})).status_code == 401

        read = await client.get(path, headers=headers)
        assert read.status_code == 200
        assert read.json()["display_name"] == "Manson"
        assert read.json()["lifecycle_state"] == "active"
        assert read.json()["operation"] == "owner.identity"

        renamed = await client.patch(
            path, json={"display_name": "  曼森  "}, headers=headers
        )
        assert renamed.status_code == 200
        assert renamed.json()["display_name"] == "曼森"
        assert renamed.json()["owner_id"] == owner_id
        assert (await client.get(path, headers=headers)).json()["display_name"] == "曼森"

        # A name is not something this surface may take away.
        blank = await client.patch(path, json={"display_name": "   "}, headers=headers)
        assert blank.status_code == 422
        assert (await client.get(path, headers=headers)).json()["display_name"] == "曼森"

        missing = await client.patch(
            "/api/workspace-authority/v1/owners/owner-nobody",
            json={"display_name": "谁"},
            headers=headers,
        )
        assert missing.status_code == 404


async def test_the_workspace_operation_survives_the_owner_changing_default(
    tmp_path,
) -> None:
    """Adding a second Eidolon and making it the default must not brick the Host.

    The reconstruction used to require that the Owner's *current* default
    Companion still be the one the workspace was initialized with. On a real
    Host, "设为默认" therefore turned this read into a permanent 409 — and the
    workspace status read gates the device list, the Companion list and the
    cockpit, so the whole product went dark after an action the product itself
    offers. What this endpoint answers is what one initialization created, not
    how the Owner has arranged things since.
    """

    settings = DataSettings(sqlite_path=str(tmp_path / "workspace-default.sqlite3"))
    store = DataStore.open(settings)
    await store.init_schema()
    await store.close()
    token = "workspace-authority-token-000002"
    operation_id = "2f0a5f0c-9d1e-4a2b-8c3d-1e4f5a6b7c8d"
    path = f"/api/workspace-authority/v1/operations/{operation_id}"
    payload = {
        "owner_display_name": "Manson",
        "companion_display_name": "Xiaoyi",
    }
    app = create_app(settings, service_token=token)
    headers = {"Authorization": f"Bearer {token}"}
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://data.test",
        ) as client,
    ):
        created = await client.put(path, json=payload, headers=headers)
        assert created.status_code == 200, created.text
        owner_id = created.json()["owner"]["owner_id"]
        first_companion = created.json()["workspace"]["primary_companion_id"]

        second = await client.put(
            f"/api/workspace-authority/v1/owners/{owner_id}"
            "/companion-provisions/6b1c2d3e-4f50-4a61-9b72-8c93da4eb5f6",
            json={"companion_display_name": "Xiaoer"},
            headers=headers,
        )
        assert second.status_code in {200, 201}, second.text
        second_companion = second.json()["companion"]["companion_id"]
        assert second_companion != first_companion

        promoted = await client.put(
            f"/api/workspace-authority/v1/owners/{owner_id}/default-companion",
            json={"companion_id": second_companion},
            headers=headers,
        )
        assert promoted.status_code == 200, promoted.text

        resumed = await client.get(path, headers=headers)
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["workspace"]["primary_companion_id"] == first_companion
