from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from eidolon_sdk.biz.system_data import (
    SystemDataNotFound,
    SystemDataRuntimeClient,
    SystemDataUpstreamError,
)

from eidolon_data import DataSettings, DataStore

pytestmark = pytest.mark.e2e
ROOT = Path(__file__).resolve().parents[2]


async def test_real_authority_process_serves_authenticated_concurrent_reads(tmp_path) -> None:
    database = tmp_path / "system.sqlite3"
    socket_path = Path("/private/tmp") / f"eidolon-data-{uuid4().hex[:12]}.sock"
    seed = DataStore.open(DataSettings(sqlite_path=str(database)))
    await seed.init_schema()
    await seed.owner_commands.create_owner(owner_id="owner-e2e")
    await seed.companion_workspaces.provision_workspace(
        owner_id="owner-e2e",
        companion_id="companion-e2e",
        genome_id="genome-e2e",
        realm_id="realm-e2e",
        role="primary",
    )
    await seed.close()

    token = "e2e-companion-authority-token-0001"
    environment = {
        **os.environ,
        "EIDOLON_DATA_SQLITE_PATH": str(database),
        "EIDOLON_DATA_COMPANION_AUTHORITY_TOKEN": token,
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "eidolon_data.api.companion_authority:create_app",
            "--factory",
            "--uds",
            str(socket_path),
            "--log-level",
            "warning",
        ],
        cwd=str(ROOT),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(100):
            if socket_path.exists():
                break
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                pytest.fail(f"authority exited early\nstdout={stdout}\nstderr={stderr}")
            await asyncio.sleep(0.05)
        else:
            pytest.fail("authority Unix socket was not ready within five seconds")

        transport = httpx.AsyncHTTPTransport(uds=str(socket_path))
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://data.local",
        ) as client:
            path = "/api/companion-authority/v1/companions/companion-e2e"
            headers = {"Authorization": f"Bearer {token}"}
            responses = await asyncio.gather(
                *(client.get(path, headers=headers) for _ in range(30))
            )
            assert {response.status_code for response in responses} == {200}
            assert {response.json()["owner_id"] for response in responses} == {"owner-e2e"}
            assert (
                await client.get(
                    path.replace("companion-e2e", "missing"),
                    headers=headers,
                )
            ).status_code == 404

            runtime = SystemDataRuntimeClient(
                client,
                "http://data.local",
                service_token=token,
            )
            snapshots = await asyncio.gather(
                *(runtime.get_companion_runtime("companion-e2e") for _ in range(30))
            )
            assert {snapshot.owner_id for snapshot in snapshots} == {"owner-e2e"}
            assert {snapshot.memory_realm.realm_id for snapshot in snapshots} == {"realm-e2e"}
            assert {snapshot.persona_genome.genome_id for snapshot in snapshots} == {"genome-e2e"}
            assert (await runtime.get_owner_primary_runtime("owner-e2e")) == snapshots[0]
            assert await runtime.get_companion_face("companion-e2e") is None
            with pytest.raises(SystemDataNotFound):
                await runtime.get_companion_runtime("missing")

            unauthorized = SystemDataRuntimeClient(
                client,
                "http://data.local",
                service_token="wrong-e2e-token",
            )
            with pytest.raises(SystemDataUpstreamError) as exc_info:
                await unauthorized.get_companion_runtime("companion-e2e")
            assert exc_info.value.status_code == 403
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        socket_path.unlink(missing_ok=True)


async def test_real_workspace_authority_process_is_concurrently_idempotent(tmp_path) -> None:
    database = tmp_path / "workspace-system.sqlite3"
    socket_path = Path("/private/tmp") / f"eidolon-workspace-{uuid4().hex[:12]}.sock"
    seed = DataStore.open(DataSettings(sqlite_path=str(database)))
    await seed.init_schema()
    await seed.close()

    token = "e2e-workspace-authority-token-0001"
    operation_id = "f8b886ff-d2e5-4d73-bb10-53d4a43f319e"
    environment = {
        **os.environ,
        "EIDOLON_DATA_SQLITE_PATH": str(database),
        "EIDOLON_DATA_WORKSPACE_AUTHORITY_TOKEN": token,
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "eidolon_data.api.workspace_authority:create_app",
            "--factory",
            "--uds",
            str(socket_path),
            "--log-level",
            "warning",
        ],
        cwd=str(ROOT),
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        for _ in range(100):
            if socket_path.exists():
                break
            if process.poll() is not None:
                stdout, stderr = process.communicate()
                pytest.fail(f"workspace authority exited early\nstdout={stdout}\nstderr={stderr}")
            await asyncio.sleep(0.05)
        else:
            pytest.fail("workspace authority Unix socket was not ready within five seconds")

        transport = httpx.AsyncHTTPTransport(uds=str(socket_path))
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://data.local",
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            path = f"/api/workspace-authority/v1/operations/{operation_id}"
            payload = {
                "owner_display_name": "Manson",
                "companion_display_name": "Eidolon",
            }
            responses = await asyncio.gather(*(client.put(path, json=payload) for _ in range(20)))
            assert {response.status_code for response in responses} == {200}
            assert len({response.text for response in responses}) == 1
            assert (await client.get(path)).json() == responses[0].json()
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        if process.stdout is not None:
            process.stdout.close()
        if process.stderr is not None:
            process.stderr.close()
        socket_path.unlink(missing_ok=True)

    persisted = DataStore.open(DataSettings(sqlite_path=str(database)))
    try:
        owners = await persisted.owners.list()
        assert len(owners) == 1
        companions = await persisted.companions.list_for_owner(owners[0].owner_id)
        assert len(companions) == 1
        assert companions[0].role == "primary"
        assert len(await persisted.memory_realms.list_for_owner(owners[0].owner_id)) == 1
    finally:
        await persisted.close()
