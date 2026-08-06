from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
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


async def _seed(path) -> DataSettings:
    settings = DataSettings(sqlite_path=str(path))
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
    app = create_app(settings, service_token=token)
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


@pytest.mark.parametrize("token", ["", "short", " " * 30])
def test_companion_authority_rejects_weak_service_tokens(token: str) -> None:
    with pytest.raises(RuntimeError, match="at least 24"):
        create_app(DataSettings(sqlite_path=":memory:"), service_token=token)
