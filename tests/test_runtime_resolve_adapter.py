from __future__ import annotations

import pytest
from eidolon_sdk.biz.admin import AdminResolvePrecondition
from eidolon_sdk.biz.registry.models import (
    AgentMetadataRecord,
    DeviceBindingRecord,
    UserRegistryRecord,
)

from eidolon_data import DataSettings, DataStore
from eidolon_data.adapters import (
    EidolonDataAgentMetadataRepository,
    EidolonDataDeviceBindingRepository,
    EidolonDataResolveClient,
    EidolonDataUserRepository,
)


async def test_runtime_resolve_user_and_device_from_eidolon_data(tmp_path) -> None:
    store = DataStore.open(DataSettings(sqlite_path=str(tmp_path / "eidolon.sqlite3")))
    try:
        users = EidolonDataUserRepository(store)
        agents = EidolonDataAgentMetadataRepository(store)
        bindings = EidolonDataDeviceBindingRepository(store)
        resolver = EidolonDataResolveClient(store)

        await users.put(
            UserRegistryRecord(
                user_id="alice",
                tenant_id="default",
                active_agent_id="companion-a",
                display_name="Alice",
                memory_port=18101,
                created_at="2026-06-27T00:00:00+00:00",
            )
        )
        await agents.put(
            AgentMetadataRecord(
                agent_id="companion-a",
                tenant_id="default",
                user_id="alice",
                template_id="xiaoyi",
                template_revision=1,
                display_name="Xiaoyi",
                created_at="2026-06-27T00:01:00+00:00",
            )
        )
        await bindings.put(
            DeviceBindingRecord(
                device_id="esp32-a",
                agent_id="companion-a",
                bound_at="2026-06-27T00:02:00+00:00",
            )
        )

        user_ctx = await resolver.resolve_user("alice")
        assert user_ctx.user_id == "alice"
        assert user_ctx.agent_id == "companion-a"
        assert user_ctx.template_id == "xiaoyi"
        assert user_ctx.memory_mcp_url == "http://127.0.0.1:18101/mcp"
        assert user_ctx.device_id is None

        device_ctx = await resolver.resolve_device("esp32-a")
        assert device_ctx.user_id == "alice"
        assert device_ctx.agent_id == "companion-a"
        assert device_ctx.device_id == "esp32-a"
    finally:
        await store.close()


async def test_runtime_resolve_rejects_unbound_device(tmp_path) -> None:
    store = DataStore.open(DataSettings(sqlite_path=str(tmp_path / "eidolon.sqlite3")))
    try:
        resolver = EidolonDataResolveClient(store)
        with pytest.raises(AdminResolvePrecondition, match="not bound"):
            await resolver.resolve_device("missing-device")
    finally:
        await store.close()
