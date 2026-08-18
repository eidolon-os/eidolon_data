"""Proof that Kernel consumes the real Data authority response."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import httpx
import pytest
import yaml

from eidolon_data import DataSettings, DataStore
from eidolon_data.api.companion_authority import create_app

pytestmark = pytest.mark.integration

KERNEL_ROOT = Path(
    os.environ.get(
        "EIDOLON_TEST_KERNEL_SOURCE_ROOT",
        Path(__file__).resolve().parents[3] / "eidolon_kernel",
    )
).resolve()
if not (KERNEL_ROOT / "eidolon_kernel").is_dir():
    pytest.skip("eidolon_kernel sibling is required", allow_module_level=True)
sys.path.insert(0, str(KERNEL_ROOT))

EidolonDataHttpCompanionAuthority = importlib.import_module(
    "eidolon_kernel.adapters.companion.eidolon_data_http"
).EidolonDataHttpCompanionAuthority
ContractRegistry = importlib.import_module("eidolon_kernel.contracts.registry").ContractRegistry


def test_kernel_manifest_publishes_exact_data_authority_contracts() -> None:
    manifest = yaml.safe_load(
        (KERNEL_ROOT / "config/system-services.yaml").read_text(encoding="utf-8")
    )
    services = {item["service_id"]: item for item in manifest["services"]}

    assert services["data"] == {
        "service_id": "data",
        "description": "System Data and Companion identity authority",
        "required": True,
        "enabled_by_default": True,
        "dependencies": [],
        # One manifest now carries every host driver; Data cares that its own
        # entry names both, because a driver left out is a Host where Data does
        # not run at all.
        "host_targets": {
            "systemd": "eidolon-data.service",
            "supervisord": "data:data-api",
        },
        "endpoints": [
            {
                "endpoint_id": "companion-authority.http",
                "protocol": "http",
                "address": "http://127.0.0.1:8084",
                "contract": (
                    "https://eidolon.dev/data/contracts/v1/companion/identity.schema.json"
                ),
                "health_url": "http://127.0.0.1:8084/health",
            },
            {
                "endpoint_id": "companion-runtime-authority.http",
                "protocol": "http",
                "address": "http://127.0.0.1:8084",
                "contract": (
                    "https://eidolon.dev/data/contracts/v1/companion/"
                    "runtime-snapshot.schema.json"
                ),
                "health_url": "http://127.0.0.1:8084/health",
            },
            {
                "endpoint_id": "memory-runtime-roster.http",
                "protocol": "http",
                "address": "http://127.0.0.1:8084",
                "contract": (
                    "https://eidolon.dev/data/contracts/v1/memory/"
                    "runtime-roster.schema.json"
                ),
                "health_url": "http://127.0.0.1:8084/health",
            },
        ],
    }
    assert services["data-workspace"] == {
        "service_id": "data-workspace",
        "description": "System Data workspace onboarding write authority",
        "required": True,
        "enabled_by_default": True,
        "dependencies": ["data"],
        "host_targets": {
            "systemd": "eidolon-data-workspace.service",
            "supervisord": "data:data-workspace-api",
        },
        "endpoints": [
            {
                "endpoint_id": "workspace-authority.http",
                "protocol": "http",
                "address": "http://127.0.0.1:8085",
                "contract": (
                    "https://eidolon.live/contracts/system-data/workspace/"
                    "onboarding-operation-v1.schema.json"
                ),
                "health_url": "http://127.0.0.1:8085/health",
            }
        ],
    }


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
    app = create_app(
        settings,
        service_token=token,
        memory_roster_token="memory-runtime-roster-token-0001",
    )
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
