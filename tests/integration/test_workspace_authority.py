from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from jsonschema import Draft202012Validator

from eidolon_data import DataSettings, DataStore
from eidolon_data.api.workspace_authority import create_app

pytestmark = pytest.mark.integration
OPERATION_SCHEMA = (
    Path(__file__).resolve().parents[2]
    / "eidolon_data"
    / "contracts"
    / "schemas"
    / "workspace"
    / "onboarding-operation.schema.json"
)


async def test_workspace_authority_auth_idempotency_and_query(tmp_path) -> None:
    settings = DataSettings(sqlite_path=str(tmp_path / "workspace-authority.sqlite3"))
    store = DataStore.open(settings)
    await store.init_schema()
    await store.close()
    token = "workspace-authority-token-000001"
    operation_id = "f8b886ff-d2e5-4d73-bb10-53d4a43f319e"
    path = f"/api/workspace-authority/v1/operations/{operation_id}"
    payload = {
        "owner_display_name": "Manson",
        "companion_display_name": "Eidolon",
    }
    app = create_app(settings, service_token=token)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://data.test",
        ) as client,
    ):
        assert (await client.get("/health")).json() == {"status": "ready"}
        assert (await client.put(path, json=payload)).status_code == 401
        assert (
            await client.put(
                path,
                json=payload,
                headers={"Authorization": "Bearer wrong-token-value"},
            )
        ).status_code == 403
        headers = {"Authorization": f"Bearer {token}"}
        first = await client.put(path, json=payload, headers=headers)
        assert first.status_code == 200
        body = first.json()
        assert body["operation_id"] == operation_id
        assert body["status"] == "succeeded"
        assert body["workspace"]["state"] == "ready"
        assert body["owner"]["owner_id"] == "owner_f8b886ffd2e54d73bb1053d4a43f319e"
        Draft202012Validator(json.loads(OPERATION_SCHEMA.read_text(encoding="utf-8"))).validate(
            body
        )

        replay = await client.put(path, json=payload, headers=headers)
        queried = await client.get(path, headers=headers)
        assert replay.status_code == 200
        assert replay.json() == body
        assert queried.status_code == 200
        assert queried.json() == body

        conflict = await client.put(
            path,
            json={**payload, "companion_display_name": "Different"},
            headers=headers,
        )
        assert conflict.status_code == 409
        assert "already in use" in conflict.json()["detail"]


async def test_workspace_authority_rejects_unknown_or_invalid_operations(tmp_path) -> None:
    settings = DataSettings(sqlite_path=str(tmp_path / "workspace-authority-errors.sqlite3"))
    store = DataStore.open(settings)
    await store.init_schema()
    await store.close()
    token = "workspace-authority-token-000002"
    headers = {"Authorization": f"Bearer {token}"}
    app = create_app(settings, service_token=token)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://data.test",
        ) as client,
    ):
        missing = await client.get(
            "/api/workspace-authority/v1/operations/7cab9151-c46a-4c90-a523-7e140ce49225",
            headers=headers,
        )
        invalid = await client.put(
            "/api/workspace-authority/v1/operations/not-a-uuid",
            headers=headers,
            json={"owner_display_name": "Owner"},
        )
        extra = await client.put(
            "/api/workspace-authority/v1/operations/7cab9151-c46a-4c90-a523-7e140ce49225",
            headers=headers,
            json={"owner_display_name": "Owner", "owner_id": "client-chosen"},
        )
        assert missing.status_code == 404
        assert invalid.status_code == 422
        assert extra.status_code == 422


async def test_owner_name_is_readable_and_correctable(tmp_path) -> None:
    """The name a person gives at first use is theirs to fix later."""

    settings = DataSettings(sqlite_path=str(tmp_path / "owner-rename.sqlite3"))
    store = DataStore.open(settings)
    await store.init_schema()
    await store.close()
    token = "workspace-authority-token-000002"
    operation_id = "0f1d0a5c-2f2e-4a1e-9a4a-1a2b3c4d5e6f"
    headers = {"Authorization": f"Bearer {token}"}
    app = create_app(settings, service_token=token)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://data.test",
        ) as client,
    ):
        created = await client.put(
            f"/api/workspace-authority/v1/operations/{operation_id}",
            json={"owner_display_name": "Manson", "companion_display_name": "小忆"},
            headers=headers,
        )
        assert created.status_code == 200
        owner_id = created.json()["owner"]["owner_id"]
        path = f"/api/workspace-authority/v1/owners/{owner_id}"

        assert (await client.get(path)).status_code == 401
        assert (await client.patch(path, json={"display_name": "x"})).status_code == 401

        read = await client.get(path, headers=headers)
        assert read.status_code == 200
        assert read.json()["display_name"] == "Manson"
        assert read.json()["lifecycle_state"] == "active"
        assert read.json()["operation"] == "owner.identity"

        renamed = await client.patch(path, json={"display_name": "  曼森  "}, headers=headers)
        assert renamed.status_code == 200
        assert renamed.json()["display_name"] == "曼森"
        assert renamed.json()["owner_id"] == owner_id
        assert (await client.get(path, headers=headers)).json()["display_name"] == "曼森"

        # A name is not something this surface may take away.
        blank = await client.patch(path, json={"display_name": "   "}, headers=headers)
        assert blank.status_code == 422
        assert (await client.get(path, headers=headers)).json()["display_name"] == "曼森"

        missing = await client.patch(
            "/api/workspace-authority/v1/owners/owner-nobody",
            json={"display_name": "谁"},
            headers=headers,
        )
        assert missing.status_code == 404


async def test_the_workspace_operation_survives_the_owner_changing_default(
    tmp_path,
) -> None:
    """Adding a second Eidolon and making it the default must not brick the Host.

    The reconstruction used to require that the Owner's *current* default
    Companion still be the one the workspace was initialized with. On a real
    Host, "设为默认" therefore turned this read into a permanent 409 — and the
    workspace status read gates the device list, the Companion list and the
    cockpit, so the whole product went dark after an action the product itself
    offers. What this endpoint answers is what one initialization created, not
    how the Owner has arranged things since.
    """

    settings = DataSettings(sqlite_path=str(tmp_path / "workspace-default.sqlite3"))
    store = DataStore.open(settings)
    await store.init_schema()
    await store.close()
    token = "workspace-authority-token-000002"
    operation_id = "2f0a5f0c-9d1e-4a2b-8c3d-1e4f5a6b7c8d"
    path = f"/api/workspace-authority/v1/operations/{operation_id}"
    payload = {
        "owner_display_name": "Manson",
        "companion_display_name": "Xiaoyi",
    }
    app = create_app(settings, service_token=token)
    headers = {"Authorization": f"Bearer {token}"}
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://data.test",
        ) as client,
    ):
        created = await client.put(path, json=payload, headers=headers)
        assert created.status_code == 200, created.text
        owner_id = created.json()["owner"]["owner_id"]
        first_companion = created.json()["workspace"]["primary_companion_id"]

        second = await client.put(
            f"/api/workspace-authority/v1/owners/{owner_id}"
            "/companion-provisions/6b1c2d3e-4f50-4a61-9b72-8c93da4eb5f6",
            json={"companion_display_name": "Xiaoer"},
            headers=headers,
        )
        assert second.status_code in {200, 201}, second.text
        second_companion = second.json()["companion"]["companion_id"]
        assert second_companion != first_companion

        promoted = await client.put(
            f"/api/workspace-authority/v1/owners/{owner_id}/default-companion",
            json={"companion_id": second_companion},
            headers=headers,
        )
        assert promoted.status_code == 200, promoted.text

        resumed = await client.get(path, headers=headers)
        assert resumed.status_code == 200, resumed.text
        assert resumed.json()["workspace"]["primary_companion_id"] == first_companion


@pytest.mark.parametrize("use_preset", [True, False])
async def test_first_companion_keeps_full_authoring_and_content_bound_retry(tmp_path, use_preset):
    from sqlalchemy import select

    from eidolon_data.schema import CompanionRow, PersonaGenomeRow
    from eidolon_data.services.persona_presets import load_persona_presets

    settings = DataSettings(sqlite_path=str(tmp_path / "authored-first.sqlite3"))
    store = DataStore.open(settings)
    await store.init_schema()
    preset = load_persona_presets().presets[2]
    persona = preset.persona.model_copy(deep=True)
    if not use_preset:
        persona.character_portrait = "由用户自定义的第一位伙伴"
    payload = {
        "owner_display_name": "Owner",
        "companion_display_name": "第一位伙伴",
        "persona": persona.model_dump(mode="json"),
        "preferences": {
            "response_length": "detailed",
            "advice": "when_asked",
            "follow_up": "when_needed",
        },
        **(
            {"source_preset_id": preset.preset_id, "source_preset_revision": preset.revision}
            if use_preset
            else {}
        ),
    }
    token = "workspace-authority-token-authored"
    headers = {"Authorization": f"Bearer {token}"}
    path = "/api/workspace-authority/v1/operations/376ce102-149f-41e6-a2a5-b3f205e41d5b"
    app = create_app(settings, service_token=token)
    try:
        async with (
            app.router.lifespan_context(app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://data.test"
            ) as http,
        ):
            first = await http.put(path, json=payload, headers=headers)
            assert first.status_code == 200, first.text
            replay = await http.put(path, json=payload, headers=headers)
            assert replay.json() == first.json()
            changed = {
                **payload,
                "preferences": {**payload["preferences"], "response_length": "brief"},
            }
            assert (await http.put(path, json=changed, headers=headers)).status_code == 409
            changed = {
                **payload,
                "persona": {**payload["persona"], "character_portrait": "换一种性格"},
            }
            assert (await http.put(path, json=changed, headers=headers)).status_code == 409
            companion_id = first.json()["workspace"]["primary_companion_id"]
            snapshot = await store.persona_commands.read_edit_snapshot(companion_id)
            assert snapshot.persona == persona
            async with store._engine.connect() as connection:
                companions = (await connection.execute(select(CompanionRow))).mappings().all()
                assert len(companions) == 1
                assert (
                    companions[0]["runtime_config_json"]["conversation_preferences"][
                        "response_length"
                    ]
                    == "detailed"
                )
                genome = (await connection.execute(select(PersonaGenomeRow))).mappings().one()
                provenance = genome["genome_json"]["provenance"]
                assert provenance["origin"] == ("template" if use_preset else "owner_authored")
                assert provenance.get("source_preset_id") == (
                    preset.preset_id if use_preset else None
                )
    finally:
        await store.close()


def test_legacy_onboarding_fingerprint_ignores_only_absent_new_fields():
    import hashlib

    from eidolon_data.api.workspace_authority import (
        WorkspaceInitializeRequest,
        _request_fingerprint,
    )

    legacy = {"owner_display_name": "Owner", "companion_display_name": "Eidolon"}
    expected = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(legacy, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert _request_fingerprint(WorkspaceInitializeRequest(**legacy)) == expected
    assert _request_fingerprint(WorkspaceInitializeRequest(**legacy, persona=None)) == expected
