"""Proof that Kernel consumes the real Data authority response."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import httpx
import pytest

from eidolon_data import DataSettings, DataStore
from eidolon_data.api.companion_authority import create_app

pytestmark = pytest.mark.integration

KERNEL_ROOT = Path(__file__).resolve().parents[3] / "eidolon_kernel"
if not (KERNEL_ROOT / "eidolon_kernel").is_dir():
    pytest.skip("eidolon_kernel sibling is required", allow_module_level=True)
sys.path.insert(0, str(KERNEL_ROOT))

EidolonDataHttpCompanionAuthority = importlib.import_module(
    "eidolon_kernel.adapters.companion.eidolon_data_http"
).EidolonDataHttpCompanionAuthority
ContractRegistry = importlib.import_module("eidolon_kernel.contracts.registry").ContractRegistry


async def test_kernel_adapter_parses_real_companion_authority_response(tmp_path) -> None:
    settings = DataSettings(sqlite_path=str(tmp_path / "joint.sqlite3"))
    seed = DataStore.open(settings)
    await seed.init_schema()
    await seed.owner_commands.create_owner(owner_id="joint-owner-1")
    await seed.companion_workspaces.provision_workspace(
        owner_id="joint-owner-1",
        companion_id="joint-companion-1",
        genome_id="joint-genome-1",
        realm_id="joint-realm-1",
    )
    await seed.close()

    token = "joint-companion-authority-token-0001"
    app = create_app(settings, service_token=token)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://data.test",
        ) as data_client,
    ):
        authority = EidolonDataHttpCompanionAuthority(
            base_url="http://data.test",
            bearer_token=token,
            contracts=ContractRegistry(),
            client=data_client,
        )
        identity = await authority.get_companion(companion_id="joint-companion-1")

    assert identity.companion_id == "joint-companion-1"
    assert identity.owner_id == "joint-owner-1"
    assert identity.status == "active"
