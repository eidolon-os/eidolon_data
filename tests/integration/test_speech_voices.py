from __future__ import annotations

import base64
import io
import json
import sqlite3
import wave
from contextlib import closing
from pathlib import Path

import httpx
import pytest

from eidolon_data import DataSettings, DataStore
from eidolon_data.api.companion_authority import create_app

pytestmark = pytest.mark.integration
TOKEN = "companion-authority-token-000001"
API = "/api/companion-authority/v1"


def _wav(seconds: float = 1.0) -> str:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1), out.setsampwidth(2), out.setframerate(24000)
        out.writeframes(b"\x01\x00" * int(24000 * seconds))
    return base64.b64encode(buffer.getvalue()).decode()


def _binding(voice_id: str, *, model: str = "qwen-audio-3.1-tts-flash", status: str = "ready") -> dict:
    return {"provider": "bailian", "model": model, "provider_voice_id": voice_id, "status": status}


def _design(operation: str, voice_id: str, *, state: str = "draft") -> dict:
    return {
        "operation_id": operation, "name": "小禾的声音", "method": "design", "state": state,
        "design_prompt": "二十出头的女生，声音清亮", "design_preview_text": "你好呀，我是你的新伙伴，今天想聊点什么？",
        "preview_wav_base64": _wav(), "binding": _binding(voice_id),
    }


def _clone(operation: str, voice_id: str) -> dict:
    return {
        "operation_id": operation, "name": "我的声音", "method": "clone", "state": "active",
        "consent": {"method": "spoken", "subject": "self", "consent_version": "2026-10-31",
                    "statement": "我同意用我的声音为 Eidolon 生成一个声音。",
                    "accepted_at": "2026-10-31T00:00:00Z"},
        "reference_wav_base64": _wav(12), "binding": _binding(voice_id, status="deploying"),
    }


@pytest.fixture
async def api(tmp_path):
    settings = DataSettings(sqlite_path=str(tmp_path / "data.sqlite3"), object_store_path=str(tmp_path / "objects"))
    writer = DataStore.open(settings)
    await writer.init_schema()
    await writer.owner_commands.create_owner(owner_id="owner-1")
    for companion in ("companion-1", "companion-2"):
        await writer.companion_workspaces.provision_workspace(
            owner_id="owner-1", companion_id=companion, genome_id=f"genome-{companion}",
            realm_id=f"realm-{companion}", kind="conversational",
        )
    await writer.close()
    app = create_app(settings, service_token=TOKEN, memory_roster_token="memory-runtime-roster-token-0001")
    async with app.router.lifespan_context(app), httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://data.test",
        headers={"Authorization": f"Bearer {TOKEN}"},
    ) as client:
        client.settings = settings
        yield client


async def test_a_companion_follows_the_host_until_its_owner_chooses(api) -> None:
    fresh = (await api.get(f"{API}/companions/companion-1/speech-voice")).json()
    assert fresh == {"companion_id": "companion-1", "revision": 0, "selection": None,
                     "record_state": "valid", "owner_voice": None}

    choice = {"source": "catalog", "provider": "bailian", "model": "cosyvoice-v3-flash",
              "voice_id": "longanhuan_v3", "display_name": "龙安欢"}
    chosen = await api.put(f"{API}/companions/companion-1/speech-voice",
                           json={"expected_revision": 0, "selection": choice})
    assert chosen.status_code == 200
    body = chosen.json()
    assert body["revision"] == 1 and body["selection"]["voice_id"] == "longanhuan_v3"
    assert body["selection"]["selected_at"]

    stale = await api.put(f"{API}/companions/companion-1/speech-voice",
                          json={"expected_revision": 0, "selection": None})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "speech_voice_changed"

    again = await api.put(f"{API}/companions/companion-1/speech-voice",
                          json={"expected_revision": 1, "selection": choice})
    assert again.json()["revision"] == 1  # the same choice is not a change

    snapshot = await api.get(f"{API}/companions/companion-1/runtime-snapshot")
    assert snapshot.status_code == 200
    assert snapshot.json()["runtime_config"]["speech_voice"]["revision"] == 1

    reset = await api.put(f"{API}/companions/companion-1/speech-voice",
                          json={"expected_revision": 1, "selection": None})
    assert reset.json()["revision"] == 2 and reset.json()["selection"] is None


async def test_a_broken_record_reads_as_no_choice_and_never_blocks_the_runtime(api) -> None:
    with closing(sqlite3.connect(api.settings.sqlite_path)) as db:
        db.execute(
            "UPDATE companions SET runtime_config_json = ? WHERE companion_id = 'companion-1'",
            (json.dumps({"speech_voice": {"revision": 4, "selection": {"source": "mystery"}}}),),
        )
        db.commit()
    voice = (await api.get(f"{API}/companions/companion-1/speech-voice")).json()
    assert voice["record_state"] == "invalid" and voice["revision"] == 4 and voice["selection"] is None
    assert (await api.get(f"{API}/companions/companion-1/runtime-snapshot")).status_code == 200
    repaired = await api.put(f"{API}/companions/companion-1/speech-voice",
                             json={"expected_revision": 4, "selection": None})
    assert repaired.status_code == 200 and repaired.json()["record_state"] == "valid"


async def test_a_designed_voice_is_a_draft_until_kept_and_creation_is_idempotent(api) -> None:
    created = await api.post(f"{API}/owners/owner-1/voices", json=_design("op-design-1", "m-vd-e1-a"))
    assert created.status_code == 200
    voice = created.json()
    assert voice["state"] == "draft" and voice["draft_expires_at"] and voice["has_preview"]
    retried = await api.post(f"{API}/owners/owner-1/voices", json=_design("op-design-1", "m-vd-e1-other"))
    assert retried.json()["voice_asset_id"] == voice["voice_asset_id"]

    preview = await api.get(f"{API}/owners/owner-1/voices/{voice['voice_asset_id']}/preview")
    assert preview.status_code == 200 and preview.content[:4] == b"RIFF"

    # A draft cannot be chosen yet.
    refused = await api.put(f"{API}/companions/companion-1/speech-voice", json={
        "expected_revision": 0,
        "selection": {"source": "owner_voice", "voice_asset_id": voice["voice_asset_id"], "display_name": "小禾"}})
    assert refused.status_code == 422

    kept = await api.patch(f"{API}/owners/owner-1/voices/{voice['voice_asset_id']}",
                           json={"expected_revision": 1, "keep": True, "name": "小禾"})
    assert kept.json()["state"] == "active" and kept.json()["draft_expires_at"] is None
    assert kept.json()["name"] == "小禾" and kept.json()["revision"] == 2

    for operation in ("op-draft-2", "op-draft-3", "op-draft-4"):
        await api.post(f"{API}/owners/owner-1/voices", json=_design(operation, f"m-vd-{operation}"))
    over = await api.post(f"{API}/owners/owner-1/voices", json=_design("op-draft-5", "m-vd-op-d5"))
    assert over.status_code == 409 and over.json()["detail"]["code"] == "draft_limit"


async def test_a_clone_needs_consent_and_keeps_its_reference_on_this_host(api) -> None:
    missing = {**_clone("op-clone-0", "m-e1-z"), "consent": None}
    assert (await api.post(f"{API}/owners/owner-1/voices", json=missing)).status_code == 422
    voice = (await api.post(f"{API}/owners/owner-1/voices", json=_clone("op-clone-1", "m-e1-c"))).json()
    assert voice["has_reference"] and voice["consent"]["method"] == "spoken"
    assert voice["bindings"][0]["status"] == "deploying"

    pending = (await api.get(f"{API}/owner-voices/pending")).json()
    assert [v["voice_asset_id"] for v in pending] == [voice["voice_asset_id"]]
    ready = await api.put(f"{API}/owners/owner-1/voices/{voice['voice_asset_id']}/binding",
                          json=_binding("m-e1-c"))
    assert ready.json()["bindings"][0]["status"] == "ready"
    assert (await api.get(f"{API}/owner-voices/pending")).json() == []

    reference = await api.get(f"{API}/owners/owner-1/voices/{voice['voice_asset_id']}/reference")
    assert reference.status_code == 200 and reference.headers["cache-control"] == "no-store"
    dropped = await api.patch(f"{API}/owners/owner-1/voices/{voice['voice_asset_id']}",
                              json={"expected_revision": 1, "drop_reference": True})
    assert dropped.json()["has_reference"] is False
    gone = await api.get(f"{API}/owners/owner-1/voices/{voice['voice_asset_id']}/reference")
    assert gone.status_code == 404


async def test_a_chosen_owner_voice_reaches_the_runtime_read_with_its_bindings(api) -> None:
    voice = (await api.post(f"{API}/owners/owner-1/voices",
                            json=_design("op-run-1", "m-vd-run", state="active"))).json()
    selection = {"source": "owner_voice", "voice_asset_id": voice["voice_asset_id"], "display_name": "小禾的声音"}
    await api.put(f"{API}/companions/companion-1/speech-voice", json={"expected_revision": 0, "selection": selection})
    runtime = (await api.get(f"{API}/companions/companion-1/speech-voice")).json()
    assert runtime["owner_voice"]["bindings"][0]["provider_voice_id"] == "m-vd-run"
    listed = (await api.get(f"{API}/owners/owner-1/voices")).json()[0]
    assert listed["used_by"] == ["companion-1"] and listed["operation_id"] == "op-run-1"

    needing = (await api.get(f"{API}/owner-voices/needing-binding",
                             params={"provider": "bailian", "model": "cosyvoice-v3-flash"})).json()
    assert [v["voice_asset_id"] for v in needing] == [voice["voice_asset_id"]]
    assert (await api.get(f"{API}/owner-voices/needing-binding",
                          params={"provider": "bailian", "model": "qwen-audio-3.1-tts-flash"})).json() == []

    # A new voice on the same model replaces the old one, which must still be deleted at the provider.
    await api.put(f"{API}/owners/owner-1/voices/{voice['voice_asset_id']}/binding", json=_binding("m-vd-run-2"))
    tombstones = (await api.get(f"{API}/provider-voice-tombstones")).json()
    assert [(t["provider_voice_id"], t["reason"]) for t in tombstones] == [("m-vd-run", "replaced")]
    assert set((await api.get(f"{API}/provider-voices/bailian")).json()) == {"m-vd-run", "m-vd-run-2"}


async def test_deleting_a_voice_frees_its_companions_and_buries_its_provider_voices(api) -> None:
    voice = (await api.post(f"{API}/owners/owner-1/voices",
                            json=_design("op-del-1", "m-vd-del", state="active"))).json()
    selection = {"source": "owner_voice", "voice_asset_id": voice["voice_asset_id"], "display_name": "小禾的声音"}
    await api.put(f"{API}/companions/companion-2/speech-voice", json={"expected_revision": 0, "selection": selection})

    assert (await api.delete(f"{API}/owners/owner-1/voices/{voice['voice_asset_id']}")).status_code == 204
    after = (await api.get(f"{API}/companions/companion-2/speech-voice")).json()
    assert after["selection"] is None and after["revision"] == 2
    assert (await api.get(f"{API}/owners/owner-1/voices")).json() == []
    assert not list(Path(api.settings.object_store_path).rglob("*.wav"))
    tombstone = (await api.get(f"{API}/provider-voice-tombstones")).json()[0]
    assert (tombstone["provider_voice_id"], tombstone["reason"]) == ("m-vd-del", "deleted")

    assert (await api.delete(f"{API}/provider-voice-tombstones/bailian/m-vd-del")).status_code == 204
    assert (await api.get(f"{API}/provider-voice-tombstones")).json() == []


async def test_expired_drafts_are_buried(api) -> None:
    draft = (await api.post(f"{API}/owners/owner-1/voices", json=_design("op-exp-1", "m-vd-exp"))).json()
    with closing(sqlite3.connect(api.settings.sqlite_path)) as db:
        db.execute("UPDATE owner_voices SET draft_expires_at = '2020-01-01 00:00:00' WHERE voice_asset_id = ?",
                   (draft["voice_asset_id"],))
        db.commit()
    assert (await api.post(f"{API}/owner-voices/expire-drafts")).json() == {"expired": 1}
    assert [t["reason"] for t in (await api.get(f"{API}/provider-voice-tombstones")).json()] == ["draft_expired"]


async def test_owner_deletion_leaves_tombstones_for_every_provider_voice(api) -> None:
    await api.post(f"{API}/owners/owner-1/voices", json=_clone("op-own-1", "m-e1-own"))
    store = DataStore.open(api.settings)
    try:
        result = await store.owner_deletion.delete_owner("owner-1")
        assert result.deleted_rows["owner_voices"] == 1
        assert any("owner-voices" in key for key in result.object_storage_keys)
        tombstones = await store.speech_voices.tombstones()
        assert [(t.provider_voice_id, t.reason) for t in tombstones] == [("m-e1-own", "owner_deleted")]
    finally:
        await store.close()


async def test_another_owners_voice_is_not_found(api) -> None:
    voice = (await api.post(f"{API}/owners/owner-1/voices", json=_design("op-other-1", "m-vd-x"))).json()
    assert (await api.get(f"{API}/owners/someone-else/voices/{voice['voice_asset_id']}")).status_code == 404
