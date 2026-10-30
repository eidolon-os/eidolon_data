import httpx
import pytest
from fastapi import FastAPI
from eidolon_data.api.team_authority import create_team_router
from eidolon_sdk.device_foundation.v1.testing import named_device_instance_id as device

TOKEN = "team-authority-test-token-00001"
HEADERS = {"Authorization": f"Bearer {TOKEN}"}
BASE = "/api/workspace-authority/v1/owners/owner-a/teams"


@pytest.mark.asyncio
async def test_authority_authentication_save_list_conflicts(store):
    await store.owner_commands.create_owner(owner_id="owner-a")
    await store.companion_workspaces.provision_workspace(
        owner_id="owner-a",
        companion_id="partner",
        companion_display_name="伙伴",
        genome_id="genome",
        realm_id="realm",
        kind="conversational",
    )
    app = FastAPI()
    app.include_router(create_team_router(store.teams, service_token=TOKEN))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://data.test"
    ) as client:
        url = BASE + "/team-a?host_id=host"
        body = dict(
            expected_revision=0,
            configuration=dict(
                name="朋友",
                input_device_id=device("input"),
                members=[dict(companion_id="partner", output_device_id=device("output"))],
            ),
        )
        assert (await client.put(url, json=body)).status_code == 401
        created = await client.put(url, json=body, headers=HEADERS)
        assert created.status_code == 200, created.text
        assert created.json()["revision"] == 1
        assert (await client.put(url, json=body, headers=HEADERS)).json() == created.json()
        page = await client.get(BASE + "?host_id=host", headers=HEADERS)
        assert page.json()["teams"] == [created.json()]
        assert (await client.get(BASE + "?host_id=other", headers=HEADERS)).json()["teams"] == []
        forged = body | {"owner_id": "other"}
        assert (await client.put(url, json=forged, headers=HEADERS)).status_code == 422

        reservation = await client.post(BASE + "/team-a/starts?host_id=host",
            json={"expected_revision": 1, "session_id": "run-a"}, headers=HEADERS)
        assert reservation.status_code == 200, reservation.text
        delete_url = url + "&expected_revision=1"
        assert (await client.delete(delete_url, headers=HEADERS)).status_code == 409
        await store.teams.confirm_closed(owner_id="owner-a", host_id="host",
                                         team_id="team-a", session_id="run-a")
        deleted = await client.delete(delete_url, headers=HEADERS)
        assert deleted.status_code == 200, deleted.text
        assert deleted.json() == {"team_id": "team-a"}
        assert (await client.delete(delete_url, headers=HEADERS)).status_code == 200
        assert (await client.get(url, headers=HEADERS)).status_code == 404
