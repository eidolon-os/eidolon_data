"""Workspace-level proof that Kernel consumes the real Data authority response."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import httpx
import pytest

from eidolon_data import DataSettings, DataStore
from eidolon_data.api.companion_authority import create_app

KERNEL_ROOT = Path(__file__).resolve().parents[3] / "eidolon_kernel"
if not (KERNEL_ROOT / "eidolon_kernel").is_dir():
    pytest.skip("eidolon_kernel sibling is required for the joint contract test", allow_module_level=True)
sys.path.insert(0, str(KERNEL_ROOT))

EidolonDataHttpCompanionAuthority = importlib.import_module(
    "eidolon_kernel.adapters.companion.eidolon_data_http"
).EidolonDataHttpCompanionAuthority
ContractRegistry = importlib.import_module(
    "eidolon_kernel.contracts.registry"
).ContractRegistry


@pytest.mark.asyncio
async def test_kernel_adapter_parses_real_companion_authority_response(tmp_path) -> None:
    settings = DataSettings(sqlite_path=str(tmp_path / "joint-authority.sqlite3"))
    seed = DataStore.open(settings)
    await seed.init_schema()
    await seed.owners.create(owner_id="joint-owner-1", display_name="Owner")
    await seed.companions.create(
        companion_id="joint-companion-1",
        owner_id="joint-owner-1",
        display_name="Companion",
    )
    await seed.close()

    token = "joint-companion-authority-token-0001"
    app = create_app(settings, service_token=token)
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://data.test",
    ) as data_client:
        authority = EidolonDataHttpCompanionAuthority(
            base_url="http://data.test",
            bearer_token=token,
            contracts=ContractRegistry(),
            client=data_client,
        )
        identity = await authority.get_companion(
            companion_id="joint-companion-1"
        )

    assert identity.companion_id == "joint-companion-1"
    assert identity.owner_id == "joint-owner-1"
    assert identity.status == "active"
