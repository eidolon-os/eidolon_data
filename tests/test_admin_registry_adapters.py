from __future__ import annotations

from datetime import datetime, timezone

from eidolon_sdk.biz.registry.models import (
    AgentMetadataRecord,
    DeviceBindingRecord,
    TenantSpec,
    UserRegistryRecord,
)

from eidolon_data import DataSettings, DataStore
from eidolon_data.adapters import (
    EidolonDataAgentMetadataRepository,
    EidolonDataDeviceBindingRepository,
    EidolonDataTenantRepository,
    EidolonDataUserRepository,
)


async def test_admin_registry_adapters_round_trip_current_contracts(tmp_path) -> None:
    store = DataStore.open(DataSettings(sqlite_path=str(tmp_path / "eidolon.sqlite3")))
    tenants = EidolonDataTenantRepository(store)
    users = EidolonDataUserRepository(store)
    agents = EidolonDataAgentMetadataRepository(store)
    bindings = EidolonDataDeviceBindingRepository(store, owner_id="owner-default")
    try:
        tenant = TenantSpec(
            tenant_id="default",
            display_name="Default",
            created_at=datetime.now(timezone.utc),
        )
        await tenants.put(tenant)
        assert (await tenants.get("default")).display_name == "Default"
        assert await tenants.count() == 1

        await users.put(
            UserRegistryRecord(
                user_id="alice",
                tenant_id="default",
                active_agent_id="agent-1",
                display_name="Alice",
                memory_port=18101,
                created_at="2026-06-27T00:00:00+00:00",
            )
        )
        loaded_user = await users.get("alice")
        assert loaded_user is not None
        assert loaded_user.active_agent_id == "agent-1"
        assert loaded_user.memory_port == 18101
        assert (await users.list_all())["alice"].display_name == "Alice"
        assert await users.allocate_memory_port() == 18100

        await agents.put(
            AgentMetadataRecord(
                agent_id="agent-1",
                tenant_id="default",
                user_id="alice",
                template_id="xiaoyi",
                template_revision=2,
                display_name="Xiaoyi",
                created_at="2026-06-27T00:01:00+00:00",
            )
        )
        loaded_agent = await agents.get("agent-1")
        assert loaded_agent is not None
        assert loaded_agent.template_id == "xiaoyi"
        assert loaded_agent.user_id == "alice"
        assert [agent_id for agent_id, _ in await agents.list_by_user("alice")] == ["agent-1"]

        await bindings.put(
            DeviceBindingRecord(
                device_id="device-1",
                agent_id="agent-1",
                bound_at="2026-06-27T00:02:00+00:00",
                interaction_mode="voice",
            )
        )
        loaded_binding = await bindings.get("device-1")
        assert loaded_binding is not None
        assert loaded_binding.agent_id == "agent-1"
        assert loaded_binding.interaction_mode == "voice"
        assert await bindings.list_by_agent("agent-1") == ["device-1"]

        await bindings.delete("device-1")
        assert await bindings.get("device-1") is None
    finally:
        await store.close()

