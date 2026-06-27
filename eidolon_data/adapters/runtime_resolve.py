"""Runtime identity resolver backed by Eidolon Data."""

from __future__ import annotations

from eidolon_sdk.biz.admin import (
    AdminResolveNotFound,
    AdminResolvePrecondition,
    ResolvedContext,
)

from eidolon_data.adapters.admin_registry import (
    EidolonDataAgentMetadataRepository,
    EidolonDataDeviceBindingRepository,
    EidolonDataUserRepository,
)
from eidolon_data.services.datastore import DataStore


class EidolonDataResolveClient:
    """Resolve runtime user/device identities without hopping through admin HTTP."""

    def __init__(self, store: DataStore) -> None:
        self._users = EidolonDataUserRepository(store)
        self._agents = EidolonDataAgentMetadataRepository(store)
        self._bindings = EidolonDataDeviceBindingRepository(store)

    async def resolve_user(self, user_id: str) -> ResolvedContext:
        user = await self._users.get(user_id)
        if user is None:
            raise AdminResolveNotFound(f"user {user_id!r} not found")
        if not user.enabled:
            raise AdminResolvePrecondition(409, f"user {user_id!r} disabled")
        if not user.active_agent_id:
            raise AdminResolvePrecondition(409, f"user {user_id!r} has no active agent")

        agent = await self._agents.get(user.active_agent_id)
        if agent is None:
            raise AdminResolveNotFound(f"agent {user.active_agent_id!r} not found")
        return _context_from_user_agent(
            user_id=user.user_id,
            tenant_id=user.tenant_id,
            agent_id=agent.agent_id,
            template_id=agent.template_id,
            memory_port=user.memory_port,
            device_id=None,
        )

    async def resolve_device(self, device_id: str) -> ResolvedContext:
        binding = await self._bindings.get(device_id)
        if binding is None:
            raise AdminResolvePrecondition(409, f"device {device_id!r} is not bound")

        agent = await self._agents.get(binding.agent_id)
        if agent is None:
            raise AdminResolveNotFound(f"agent {binding.agent_id!r} not found")
        user = await self._users.get(agent.user_id)
        if user is None:
            raise AdminResolveNotFound(f"user {agent.user_id!r} not found")
        if not user.enabled:
            raise AdminResolvePrecondition(409, f"user {agent.user_id!r} disabled")
        return _context_from_user_agent(
            user_id=user.user_id,
            tenant_id=user.tenant_id,
            agent_id=agent.agent_id,
            template_id=agent.template_id,
            memory_port=user.memory_port,
            device_id=device_id,
        )


def _context_from_user_agent(
    *,
    user_id: str,
    tenant_id: str,
    agent_id: str,
    template_id: str | None,
    memory_port: int,
    device_id: str | None,
) -> ResolvedContext:
    memory_mcp_url = f"http://127.0.0.1:{memory_port}/mcp" if memory_port else ""
    return ResolvedContext(
        tenant_id=tenant_id,
        user_id=user_id,
        agent_id=agent_id,
        template_id=template_id,
        memory_mcp_url=memory_mcp_url,
        device_id=device_id,
    )


__all__ = ["EidolonDataResolveClient"]
