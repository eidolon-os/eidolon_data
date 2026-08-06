from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

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
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            path = "/api/companion-authority/v1/companions/companion-e2e"
            responses = await asyncio.gather(*(client.get(path) for _ in range(30)))
            assert {response.status_code for response in responses} == {200}
            assert {response.json()["owner_id"] for response in responses} == {"owner-e2e"}
            assert (await client.get(path.replace("companion-e2e", "missing"))).status_code == 404
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
