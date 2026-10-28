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
        kind="conversational",
    )
    await seed.close()

    token = "e2e-companion-authority-token-0001"
    environment = {
        **os.environ,
        "EIDOLON_DATA_SQLITE_PATH": str(database),
        "EIDOLON_DATA_COMPANION_AUTHORITY_TOKEN": token,
        "EIDOLON_DATA_MEMORY_RUNTIME_ROSTER_TOKEN": (
            "e2e-memory-runtime-roster-token-0001"
        ),
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
            assert (await runtime.get_owner_default_runtime("owner-e2e")) == snapshots[0]
            assert await runtime.get_companion_face("companion-e2e") is None
            with pytest.raises(SystemDataNotFound):
                await runtime.get_companion_runtime("missing")

            # Exercise the real Person API, SDK decoder and supervisor roster
            # together. Creating private Realms must not change the default.
            people = "/api/companion-authority/v1/owners/owner-e2e/persons"
            personal = {}
            for person in ("a" * 32, "b" * 32):
                created = await client.post(
                    people, headers=headers,
                    json={"person_id": person, "display_name": person[0]},
                )
                assert created.status_code == 200, created.text
                with pytest.raises(SystemDataNotFound):
                    await runtime.get_person_memory_realm(owner_id="owner-e2e", person_id=person)
                provisioned = await asyncio.gather(*(
                    client.put(f"{people}/{person}/memory-realm", headers=headers)
                    for _ in range(5)
                ))
                assert {r.status_code for r in provisioned} == {200}
                assert len({r.json()["realm_id"] for r in provisioned}) == 1
                personal[person] = await runtime.get_person_memory_realm(owner_id="owner-e2e", person_id=person)
                with pytest.raises(SystemDataNotFound):
                    await runtime.get_person_memory_realm(owner_id="another-owner", person_id=person)
            a, b = personal.values()
            assert len({a.realm_id, b.realm_id, "realm-e2e"}) == 3
            assert (await runtime.get_owner_default_runtime("owner-e2e")) == snapshots[0]
            roster_path = "/api/companion-authority/v1/memory-runtime-roster"
            roster_headers = {"Authorization": "Bearer e2e-memory-runtime-roster-token-0001"}
            roster = await client.get(roster_path, headers=roster_headers)
            assert roster.status_code == 200
            assert {r["realm_id"] for r in roster.json()["realms"]} == {
                "realm-e2e", a.realm_id, b.realm_id,
            }
            removed = await client.delete(
                f"{people}/{a.person_id}", headers=headers, params={"expected_revision": 1}
            )
            assert removed.status_code == 204
            with pytest.raises(SystemDataNotFound):
                await runtime.get_person_memory_realm(owner_id="owner-e2e", person_id=a.person_id)
            roster = await client.get(roster_path, headers=roster_headers)
            assert {r["realm_id"] for r in roster.json()["realms"]} == {"realm-e2e", b.realm_id}
            assert (await runtime.get_person_memory_realm(owner_id="owner-e2e", person_id=b.person_id)) == b

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
        assert companions[0].kind == "conversational"
        assert owners[0].default_companion_id == companions[0].companion_id
        assert len(await persisted.memory_realms.list_for_owner(owners[0].owner_id)) == 1
        home = await persisted.smarthome_registry.get_registry(owners[0].owner_id)
        assert home.revision == 1
        assert (len(home.devices), len(home.areas), len(home.scenes)) == (18, 7, 4)
        facts = await persisted.audit_outbox.list_pending()
        assert sum(f.action == "smarthome.registry.changed" for f in facts) == 1
    finally:
        await persisted.close()
