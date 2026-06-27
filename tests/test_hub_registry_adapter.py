from __future__ import annotations

from eidolon_sdk.biz.registry.models import DeviceBindingRecord, DeviceRegistryRecord

from eidolon_data import DataSettings, DataStore
from eidolon_data.adapters import (
    EidolonDataDeviceBindingRepository,
    EidolonDataDeviceRegistryRepository,
)


async def test_hub_device_registry_adapter_round_trips_device_record(tmp_path) -> None:
    store = DataStore.open(DataSettings(sqlite_path=str(tmp_path / "eidolon.sqlite3")))
    repo = EidolonDataDeviceRegistryRepository(store, owner_id="owner-test")
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
        assert row.auth_type == "psk"
        assert row.secret_ref == "psk_hash:hash-1"

        bindings = EidolonDataDeviceBindingRepository(store, owner_id="owner-test")
        await bindings.put(
            DeviceBindingRecord(
                device_id="device-1",
                agent_id="companion-1",
                bound_at="2026-06-27T00:02:00+00:00",
                interaction_mode="voice",
            )
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
        binding = await bindings.get("device-1")
        assert binding is not None
        assert binding.agent_id == "companion-1"
        assert binding.interaction_mode == "voice"
    finally:
        await store.close()
