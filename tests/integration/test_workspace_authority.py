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
