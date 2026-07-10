from __future__ import annotations

from eidolon_sdk.biz.registry.models import DeviceRegistryRecord

from eidolon_data import DataSettings, DataStore
from eidolon_data.adapters import EidolonDataDeviceRegistryRepository


async def test_hub_device_registry_adapter_round_trips_device_record(tmp_path) -> None:
    store = DataStore.open(DataSettings(sqlite_path=str(tmp_path / "eidolon.sqlite3")))
    repo = EidolonDataDeviceRegistryRepository(store)
    try:
        await repo.put(
            DeviceRegistryRecord(
                device_id="device-1",
                name="Desk Body",
                kind="voice_body",
                enabled=True,
                psk_hash="hash-1",
                paired=True,
                approved=True,
                approved_at="2026-06-27T00:00:00+00:00",
                created_at="2026-06-27T00:00:00+00:00",
                last_seen="2026-06-27T00:01:00+00:00",
                metadata={"room": "studio"},
            )
        )

        loaded = await repo.get("device-1")
        assert loaded is not None
        assert loaded.device_id == "device-1"
        assert loaded.name == "Desk Body"
        assert loaded.kind == "voice_body"
        assert loaded.enabled is True
        assert loaded.psk_hash == "hash-1"
        assert loaded.paired is True
        assert loaded.approved is True
        assert loaded.metadata == {"room": "studio"}

        rows = await repo.list_all()
        assert list(rows) == ["device-1"]

        row = await store.devices.get_device("device-1")
        assert row is not None
        assert row.owner_id is None
        assert row.status == "discovered"
        assert row.auth_type == "psk"
        assert row.secret_ref == "psk_hash:hash-1"

        await store.owners.create(owner_id="owner-test", display_name="Owner")
        await store.companions.create(
            companion_id="companion-1",
            owner_id="owner-test",
            display_name="Companion",
        )
        await store.devices.claim_device(
            "device-1",
            owner_id="owner-test",
            companion_id="companion-1",
            interaction_mode="voice",
            metadata_json={"claimed": True},
        )
        await store.devices.update_device(
            "device-1",
            capabilities_json={"ops": ["sound.play"]},
        )

        await repo.put(
            DeviceRegistryRecord(
                device_id="device-1",
                name="Desk Body",
                kind="voice_body",
                enabled=True,
                psk_hash="hash-2",
                paired=True,
                approved=True,
                approved_at="2026-06-27T00:03:00+00:00",
                created_at="2026-06-27T00:00:00+00:00",
                last_seen="2026-06-27T00:04:00+00:00",
                metadata={"room": "studio"},
            )
        )
        row = await store.devices.get_device("device-1")
        assert row is not None
        assert row.owner_id == "owner-test"
        assert row.bound_companion_id == "companion-1"
        assert row.status == "active"
        assert row.metadata_json["claimed"] is True
        assert row.capabilities_json == {"ops": ["sound.play"]}
        assert row.interaction_mode == "voice"
    finally:
        await store.close()


async def test_hub_device_registry_adapter_writes_declared_capabilities(tmp_path) -> None:
    from eidolon_sdk.biz.body import capabilities_from_json

    store = DataStore.open(DataSettings(sqlite_path=str(tmp_path / "eidolon.sqlite3")))
    repo = EidolonDataDeviceRegistryRepository(store)
    try:
        manifest = [
            {
                "name": "display.update",
                "description": "Update the screen",
                "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}},
            },
            {"name": "sound.play"},
        ]
        await repo.put(
            DeviceRegistryRecord(
                device_id="dev-cap", name="Box", kind="esp32", capabilities=manifest
            )
        )
        row = await store.devices.get_device("dev-cap")
        assert row is not None
        # canonical stored shape the agent's body device store reads back
        assert row.capabilities_json == {"capabilities": manifest}
        caps = capabilities_from_json(row.capabilities_json, device_kind="esp32")
        assert {c.name for c in caps} == {"display.update", "sound.play"}

        # a re-register without a manifest preserves the declared capabilities
        await repo.put(DeviceRegistryRecord(device_id="dev-cap", name="Box", kind="esp32"))
        row = await store.devices.get_device("dev-cap")
        assert row is not None
        assert row.capabilities_json == {"capabilities": manifest}
    finally:
        await store.close()
