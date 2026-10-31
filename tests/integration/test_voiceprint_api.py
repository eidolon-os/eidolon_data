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


async def test_http_channel_calibration_is_owner_scoped_public_replay(store):
    for owner in ("o", "other"):
        await store.owner_commands.create_owner(owner_id=owner)
    app = FastAPI()
    app.include_router(create_voiceprint_router(store, "secret"))
    sample = {
        "model_id": "campplus",
        "weights_hash": "b" * 64,
        "preprocessing_version": "v1",
        "vector": [0.6, 0.8],
        "speech_ms": 7800,
        "crops": [{"speech_ms": 1500, "vector": [0.0, 1.0]}, {"speech_ms": 2100, "vector": [1.0, 0.0]}],
    }
    body = {"channel": "mobile", "batch_id": "replay-1", "captured_at": 100.0, "sample": sample}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as h:
        prefix = "/api/companion-authority/v1/owners/o/voiceprints/channel-calibration"
        assert (await h.post(prefix + "/samples", json=body)).status_code == 401
        h.headers["Authorization"] = "Bearer secret"
        assert (
            await h.get(prefix, headers={"Authorization": "Bearer wrong"})
        ).status_code == 403

        r = await h.post(prefix + "/samples", json=body)
        assert r.status_code == 200, r.text
        added = r.json()
        assert added["source"] == "public_replay" and added["sample_id"].startswith("cc_")
        assert added["sample"]["crops"] == sample["crops"]
        later = body | {"captured_at": 160.0}
        assert (await h.post(prefix + "/samples", json=later)).status_code == 200
        speaker = body | {"channel": "korvo", "batch_id": "replay-2", "captured_at": 200.0}
        assert (await h.post(prefix + "/samples", json=speaker)).status_code == 200

        # Data names the sample and fixes its source; nothing else is accepted.
        for change in ({"source": "household"}, {"sample_id": "mine"}, {"batch_id": "bad id"},
                       {"captured_at": 0}, {"channel": ""}):
            assert (await h.post(prefix + "/samples", json=body | change)).status_code == 422
        moved = body | {"channel": "korvo"}
        assert (await h.post(prefix + "/samples", json=moved)).status_code == 409

        listed = await h.get(prefix + "/samples")
        assert listed.status_code == 200 and listed.headers["cache-control"] == "no-store"
        assert [s["captured_at"] for s in listed.json()] == [100.0, 160.0, 200.0]
        assert listed.json()[0] == added
        mobile = (await h.get(prefix + "/samples", params={"channel": "mobile"})).json()
        assert {s["channel"] for s in mobile} == {"mobile"} and len(mobile) == 2

        summary = (await h.get(prefix)).json()
        assert summary == {"batches": [
            {"batch_id": "replay-1", "channel": "mobile", "samples": 2,
             "first_captured_at": 100.0, "last_captured_at": 160.0},
            {"batch_id": "replay-2", "channel": "korvo", "samples": 1,
             "first_captured_at": 200.0, "last_captured_at": 200.0},
        ]}

        other = prefix.replace("/owners/o/", "/owners/other/")
        assert (await h.get(other + "/samples")).json() == []
        assert (await h.delete(other + "/batches/replay-1")).json() == {"batches": []}
        missing = prefix.replace("/owners/o/", "/owners/missing/")
        assert (await h.get(missing)).status_code == 404
        assert (await h.post(missing + "/samples", json=body)).status_code == 404

        r = await h.delete(prefix + "/batches/replay-1")
        assert r.status_code == 200
        assert [b["batch_id"] for b in r.json()["batches"]] == ["replay-2"]
        assert [s["batch_id"] for s in (await h.get(prefix + "/samples")).json()] == ["replay-2"]
