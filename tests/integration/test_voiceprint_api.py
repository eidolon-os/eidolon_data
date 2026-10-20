import base64
import httpx
import pytest
from fastapi import FastAPI
from eidolon_data.api.routers.voiceprints import create_voiceprint_router
from eidolon_sdk.biz.system_data import SystemDataRuntimeClient


async def test_http_enrollment_secrecy_and_live_revocation(store):
    await store.owner_commands.create_owner(owner_id="o")
    await store.companion_workspaces.provision_workspace(
        owner_id="o", companion_id="c", genome_id="g", realm_id="r", kind="conversational"
    )
    app = FastAPI()
    app.include_router(create_voiceprint_router(store, "secret"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as h:
        prefix = "/api/companion-authority/v1/owners/o/companions/c/voiceprints"
        assert (await h.get(prefix)).status_code == 401
        h.headers["Authorization"] = "Bearer secret"
        assert (await h.get(prefix.replace("/owners/o/", "/owners/other/"))).status_code == 404
        e = (
            await h.post(
                prefix + "/enrollments", json={"display_name": "甲", "operation_id": "one"}
            )
        ).json()
        sample = {
            "model_id": "test",
            "weights_hash": "a" * 64,
            "preprocessing_version": "v1",
            "vector": [1, 0],
            "speech_ms": 4000,
        }
        for i in range(3):
            r = await h.put(prefix + f"/enrollments/{e['enrollment_id']}/samples/{i}", json=sample)
            assert r.status_code == 200, r.text
        recording = {"pcm_base64": base64.b64encode(b"\x01\x00" * 32000).decode()}
        r = await h.post(prefix + f"/enrollments/{e['enrollment_id']}/complete", json={"sample": sample, "recording": recording})
        assert r.status_code == 200, r.text
        profile = r.json()["profile_id"]
        assert r.json()["recording_available"]
        audio_path = prefix + f"/profiles/{profile}/recording"
        audio = await h.get(audio_path)
        assert audio.status_code == 200
        assert audio.headers["cache-control"] == "no-store"
        assert audio.json()["pcm_base64"] == recording["pcm_base64"]
        assert (await h.get(audio_path, headers={"Authorization": "Bearer wrong"})).status_code == 403
        assert (await h.get(audio_path.replace("/owners/o/", "/owners/other/"))).status_code == 404
        view = (await h.get(prefix)).json()
        assert "vector" not in str(view)
        await h.put(
            prefix + "/policy", json={"mode": "allowlist", "expected_revision": view["revision"]}
        )
        runtime = SystemDataRuntimeClient(h, "http://test", service_token="secret")
        snapshot = await runtime.get_voiceprint_snapshot("c")
        assert len(snapshot.templates) == 1
        assert await runtime.admit_voiceprint("c", snapshot.revision, profile)
        assert (await h.delete(prefix + f"/bindings/{profile}")).status_code == 200
        assert not await runtime.admit_voiceprint("c", snapshot.revision, profile)


async def test_cross_owner_binding_is_rejected(store):
    from eidolon_data.schema.voiceprints import SpeakerProfileRow

    await store.owner_commands.create_owner(owner_id="a")
    await store.owner_commands.create_owner(owner_id="b")
    await store.companion_workspaces.provision_workspace(
        owner_id="a", companion_id="c", genome_id="g", realm_id="r", kind="conversational"
    )
    async with store._session_factory() as s, s.begin():
        s.add(
            SpeakerProfileRow(
                profile_id="p",
                owner_id="b",
                display_name="other",
                revision=1,
                enrollment_id="e",
                template_json={},
            )
        )
    with pytest.raises(LookupError):
        await store.voiceprints.bind("a", "c", "p")
