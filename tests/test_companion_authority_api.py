from __future__ import annotations

import httpx
import pytest

from eidolon_data import DataSettings, DataStore
from eidolon_data.api.companion_authority import create_app


@pytest.mark.asyncio
async def test_companion_authority_is_exact_read_only_and_authenticated(tmp_path) -> None:
    settings = DataSettings(sqlite_path=str(tmp_path / "authority.sqlite3"))
    seed = DataStore.open(settings)
    await seed.init_schema()
    await seed.owners.create(owner_id="owner-1", display_name="Owner")
    await seed.companions.create(
        companion_id="companion-1",
        owner_id="owner-1",
        display_name="Companion",
        profile_json={"private": "must-not-leak"},
        runtime_config_json={"credential": "must-not-leak"},
    )
    await seed.close()

    app = create_app(settings, service_token="authority-service-token-0001")
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://data.test",
    ) as client:
        missing = await client.get(
            "/api/companion-authority/v1/companions/companion-1"
        )
        wrong = await client.get(
            "/api/companion-authority/v1/companions/companion-1",
            headers={"Authorization": "Bearer wrong-service-token-0001"},
        )
        found = await client.get(
            "/api/companion-authority/v1/companions/companion-1",
            headers={"Authorization": "Bearer authority-service-token-0001"},
        )
        absent = await client.get(
            "/api/companion-authority/v1/companions/absent",
            headers={"Authorization": "Bearer authority-service-token-0001"},
        )

    assert missing.status_code == 401
    assert wrong.status_code == 403
    assert absent.status_code == 404
    assert found.status_code == 200
    assert found.json() == {
        "operation": "companion.identity",
        "companion_id": "companion-1",
        "owner_id": "owner-1",
        "lifecycle_state": "active",
    }


def test_companion_authority_rejects_missing_or_short_secret(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("EIDOLON_DATA_COMPANION_AUTHORITY_TOKEN", raising=False)
    settings = DataSettings(sqlite_path=str(tmp_path / "authority.sqlite3"))
    with pytest.raises(RuntimeError, match="at least 24"):
        create_app(settings)
    with pytest.raises(RuntimeError, match="at least 24"):
        create_app(settings, service_token="too-short")
