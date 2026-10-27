import base64
import hashlib

import httpx
from eidolon_sdk.biz.voiceprint import ExtractedSample, enrollment_evidence_digest
from fastapi import FastAPI

from eidolon_data.api.routers.voiceprints import create_voiceprint_router


async def test_http_enrollment_secrecy_and_live_revocation(store):
    await store.owner_commands.create_owner(owner_id="o")
    from eidolon_sdk.biz.person import PersonCreate
    await store.persons.create("o", PersonCreate(person_id="a" * 32, display_name="甲"))
    app = FastAPI()
    app.include_router(create_voiceprint_router(store, "secret"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as h:
        prefix = "/api/companion-authority/v1/owners/o/voiceprints"
        assert (await h.get(prefix)).status_code == 401
        h.headers["Authorization"] = "Bearer secret"
        assert (await h.get(prefix.replace("/owners/o/", "/owners/other/"))).status_code == 404
        e = (
            await h.post(
                prefix + "/enrollments", json={"display_name": "甲", "operation_id": "one", "person_id": "a" * 32}
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
        r = await h.post(
            prefix + f"/enrollments/{e['enrollment_id']}/complete",
            json={
                "evaluation": {
                    "evidence_digest": enrollment_evidence_digest(
                        {str(i): ExtractedSample.model_validate(sample) for i in range(3)}
                    ),
                    "recording_sha256": hashlib.sha256(
                        base64.b64decode(recording["pcm_base64"])
                    ).hexdigest(),
                    "template": sample,
                    "trial": sample,
                    "registration_min_similarity": 1,
                    "trial_min_similarity": 1,
                },
                "recording": recording,
            },
        )
        assert r.status_code == 200, r.text
        profile = r.json()["profile_id"]
        assert r.json()["recording_available"]
        audio_path = prefix + f"/profiles/{profile}/recording"
        audio = await h.get(audio_path)
        assert audio.status_code == 200
        assert audio.headers["cache-control"] == "no-store"
        assert audio.json()["pcm_base64"] == recording["pcm_base64"]
        assert (
            await h.get(audio_path, headers={"Authorization": "Bearer wrong"})
        ).status_code == 403
        assert (await h.get(audio_path.replace("/owners/o/", "/owners/other/"))).status_code == 404
        view = (await h.get(prefix)).json()
        assert "vector" not in str(view)
        assert view["profiles"][0]["person_id"] == "a" * 32
        assert (await h.put(prefix + "/policy", json={})).status_code == 404
        assert (await h.get("/api/companion-authority/v1/companions/c/voiceprint-snapshot")).status_code == 404
