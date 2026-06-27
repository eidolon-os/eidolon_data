"""Admin registry compatibility adapters backed by Eidolon Data.

These adapters keep the current admin orchestrators running while moving their
storage away from the old registry SQLite file and into the new sovereign data
schema. The mapping is intentionally transitional:

- tenants -> owners(kind="team") with admin metadata
- users -> owners(kind="person") with registry metadata
- agents -> companions plus persona_genomes
- device bindings -> devices.bound_companion_id plus access_policy_json
"""

from __future__ import annotations

from datetime import datetime, timezone

from eidolon_sdk.biz.registry.models import (
    AgentMetadataRecord,
    ConsolidatorConfig,
    DeviceBindingRecord,
    TenantSpec,
    UserRegistryRecord,
)
from sqlalchemy import delete, select

from eidolon_data.schema.models import CompanionRow, DeviceRow, OwnerRow
from eidolon_data.services.datastore import DataStore


class EidolonDataTenantRepository:
    def __init__(self, store: DataStore) -> None:
        self._store = store

    async def get(self, tenant_id: str) -> TenantSpec | None:
        await self._store.init_schema()
        row = await self._store.owners.get(_tenant_owner_id(tenant_id))
        return _tenant_from_owner(row) if row is not None else None

    async def put(self, spec: TenantSpec) -> None:
        await self._store.init_schema()
        owner_id = _tenant_owner_id(spec.tenant_id)
        existing = await self._store.owners.get(owner_id)
        profile_json = {"registry": {"tenant_id": spec.tenant_id}}
        settings_json = {"created_at": spec.created_at.isoformat()}
        if existing is None:
            await self._store.owners.create(
                owner_id=owner_id,
                display_name=spec.display_name,
                kind="team",
                profile_json=profile_json,
                settings_json=settings_json,
            )
            return
        async with self._store.session_factory() as session:
            row = await session.get(OwnerRow, owner_id)
            row.display_name = spec.display_name
            row.profile_json = profile_json
            row.settings_json = settings_json
            await session.commit()

    async def delete(self, tenant_id: str) -> None:
        await self._store.init_schema()
        async with self._store.session_factory() as session:
            await session.execute(delete(OwnerRow).where(OwnerRow.owner_id == _tenant_owner_id(tenant_id)))
            await session.commit()

    async def list_all(self) -> list[TenantSpec]:
        await self._store.init_schema()
        async with self._store.session_factory() as session:
            rows = await session.scalars(
                select(OwnerRow).where(OwnerRow.kind == "team").order_by(OwnerRow.created_at)
            )
            return [_tenant_from_owner(row) for row in rows if _tenant_from_owner(row) is not None]

    async def count(self) -> int:
        return len(await self.list_all())


class EidolonDataUserRepository:
    def __init__(self, store: DataStore, *, auto_port_min: int = 18100, auto_port_max: int = 18200) -> None:
        self._store = store
        self._auto_port_min = auto_port_min
        self._auto_port_max = auto_port_max

    async def get(self, user_id: str) -> UserRegistryRecord | None:
        await self._store.init_schema()
        row = await self._store.owners.get(_user_owner_id(user_id))
        return _user_from_owner(row) if row is not None else None

    async def put(self, record: UserRegistryRecord) -> None:
        await self._store.init_schema()
        owner_id = _user_owner_id(record.user_id)
        existing = await self._store.owners.get(owner_id)
        profile_json = {
            "registry": {
                "user_id": record.user_id,
                "tenant_id": record.tenant_id,
                "active_agent_id": record.active_agent_id,
                "enabled": record.enabled,
            }
        }
        settings_json = {
            "palace_path": record.palace_path,
            "memory_port": record.memory_port,
            "consolidator": record.consolidator.model_dump(),
            "created_at": record.created_at or _now_iso(),
        }
        if existing is None:
            await self._store.owners.create(
                owner_id=owner_id,
                display_name=record.display_name or record.user_id,
                kind="person",
                profile_json=profile_json,
                settings_json=settings_json,
            )
            return
        async with self._store.session_factory() as session:
            row = await session.get(OwnerRow, owner_id)
            row.display_name = record.display_name or record.user_id
            row.profile_json = profile_json
            row.settings_json = settings_json
            await session.commit()

    async def delete(self, user_id: str) -> None:
        await self._store.init_schema()
        async with self._store.session_factory() as session:
            await session.execute(delete(OwnerRow).where(OwnerRow.owner_id == _user_owner_id(user_id)))
            await session.commit()

    async def list_all(self) -> dict[str, UserRegistryRecord]:
        await self._store.init_schema()
        async with self._store.session_factory() as session:
            rows = await session.scalars(
                select(OwnerRow).where(OwnerRow.kind == "person").order_by(OwnerRow.created_at)
            )
            records = [_user_from_owner(row) for row in rows]
            return {record.user_id: record for record in records if record is not None}

    async def allocate_memory_port(self) -> int:
        records = await self.list_all()
        taken = {record.memory_port for record in records.values() if record.memory_port > 0}
        for port in range(self._auto_port_min, self._auto_port_max):
            if port not in taken:
                return port
        raise RuntimeError(f"no free memory port in [{self._auto_port_min}, {self._auto_port_max})")


class EidolonDataAgentMetadataRepository:
    def __init__(self, store: DataStore) -> None:
        self._store = store

    async def get(self, agent_id: str) -> AgentMetadataRecord | None:
        await self._store.init_schema()
        row = await self._store.companions.get(agent_id)
        return _agent_from_companion(row) if row is not None else None

    async def put(self, record: AgentMetadataRecord) -> None:
        await self._store.init_schema()
        owner_id = _user_owner_id(record.user_id)
        if await self._store.owners.get(owner_id) is None:
            await self._store.owners.create(owner_id=owner_id, display_name=record.user_id, kind="person")
        existing = await self._store.companions.get(record.agent_id)
        metadata_json = {
            "registry": {
                "tenant_id": record.tenant_id,
                "user_id": record.user_id,
                "template_id": record.template_id,
                "template_revision": record.template_revision,
                "created_at": record.created_at or _now_iso(),
            }
        }
        if existing is None:
            await self._store.companions.create(
                companion_id=record.agent_id,
                owner_id=owner_id,
                display_name=record.display_name or record.agent_id,
                kind="companion",
                metadata_json=metadata_json,
            )
        else:
            async with self._store.session_factory() as session:
                row = await session.get(CompanionRow, record.agent_id)
                row.owner_id = owner_id
                row.display_name = record.display_name or record.agent_id
                row.metadata_json = metadata_json
                await session.commit()
        genome = await self._store.persona.get_current_genome(record.agent_id)
        if genome is None:
            await self._store.persona_repo.create_genome(
                genome_id=f"genome-{record.agent_id}-1",
                companion_id=record.agent_id,
                version=1,
                source_json={
                    "source_type": "template",
                    "template_id": record.template_id,
                    "template_revision": record.template_revision,
                },
                genome_json={
                    "source_template_id": record.template_id,
                    "source_template_revision": record.template_revision,
                },
            )
            await self._store.companions.set_current_genome(record.agent_id, f"genome-{record.agent_id}-1")

    async def delete(self, agent_id: str) -> None:
        await self._store.init_schema()
        async with self._store.session_factory() as session:
            await session.execute(delete(CompanionRow).where(CompanionRow.companion_id == agent_id))
            await session.commit()

    async def list_all(self) -> dict[str, AgentMetadataRecord]:
        await self._store.init_schema()
        async with self._store.session_factory() as session:
            rows = await session.scalars(
                select(CompanionRow).where(CompanionRow.kind == "companion").order_by(CompanionRow.created_at)
            )
            records = [_agent_from_companion(row) for row in rows]
            return {record.agent_id: record for record in records if record is not None}

    async def list_by_user(self, user_id: str) -> list[tuple[str, AgentMetadataRecord]]:
        rows = await self.list_all()
        return [(agent_id, record) for agent_id, record in rows.items() if record.user_id == user_id]


class EidolonDataDeviceBindingRepository:
    def __init__(self, store: DataStore, *, owner_id: str = "owner-default") -> None:
        self._store = store
        self._owner_id = owner_id

    async def get(self, device_id: str) -> DeviceBindingRecord | None:
        await self._store.init_schema()
        row = await self._binding_for_device(device_id)
        return _binding_from_device(row) if row is not None else None

    async def put(self, record: DeviceBindingRecord) -> None:
        await self._store.init_schema()
        async with self._store.session_factory() as session:
            row = await session.get(DeviceRow, record.device_id)
            if row is None:
                row = DeviceRow(
                    device_id=record.device_id,
                    owner_id=self._owner_id,
                    status="active",
                    capabilities_json={},
                    network_json={},
                    metadata_json={},
                    access_policy_json={},
                )
                session.add(row)
            row.bound_companion_id = record.agent_id
            row.interaction_mode = record.interaction_mode
            policy = dict(row.access_policy_json or {})
            policy["bound_at"] = record.bound_at
            row.access_policy_json = policy
            await session.commit()

    async def delete(self, device_id: str) -> None:
        await self._store.init_schema()
        async with self._store.session_factory() as session:
            row = await session.get(DeviceRow, device_id)
            if row is not None:
                row.bound_companion_id = None
                row.interaction_mode = None
                policy = dict(row.access_policy_json or {})
                policy.pop("bound_at", None)
                row.access_policy_json = policy
            await session.commit()

    async def list_all(self) -> dict[str, DeviceBindingRecord]:
        await self._store.init_schema()
        async with self._store.session_factory() as session:
            rows = await session.scalars(
                select(DeviceRow)
                .where(DeviceRow.bound_companion_id.is_not(None))
                .order_by(DeviceRow.device_id)
            )
            records = [_binding_from_device(row) for row in rows]
            return {record.device_id: record for record in records}

    async def list_by_agent(self, agent_id: str) -> list[str]:
        await self._store.init_schema()
        async with self._store.session_factory() as session:
            rows = await session.scalars(
                select(DeviceRow.device_id).where(DeviceRow.bound_companion_id == agent_id)
            )
            return list(rows)

    async def _binding_for_device(self, device_id: str) -> DeviceRow | None:
        async with self._store.session_factory() as session:
            row = await session.get(DeviceRow, device_id)
            if row is None or not row.bound_companion_id:
                return None
            return row


def _tenant_owner_id(tenant_id: str) -> str:
    return f"tenant:{tenant_id}"


def _user_owner_id(user_id: str) -> str:
    return user_id


def _tenant_from_owner(row: OwnerRow | None) -> TenantSpec | None:
    if row is None:
        return None
    registry = (row.profile_json or {}).get("registry") or {}
    tenant_id = registry.get("tenant_id")
    if not tenant_id:
        return None
    return TenantSpec(
        tenant_id=tenant_id,
        display_name=row.display_name or tenant_id,
        created_at=_parse_dt((row.settings_json or {}).get("created_at")) or row.created_at,
    )


def _user_from_owner(row: OwnerRow | None) -> UserRegistryRecord | None:
    if row is None:
        return None
    registry = (row.profile_json or {}).get("registry") or {}
    user_id = registry.get("user_id") or row.owner_id
    if str(user_id).startswith("tenant:"):
        return None
    settings = row.settings_json or {}
    return UserRegistryRecord(
        user_id=user_id,
        tenant_id=registry.get("tenant_id", "default"),
        active_agent_id=registry.get("active_agent_id"),
        display_name=row.display_name or user_id,
        enabled=bool(registry.get("enabled", True)),
        palace_path=str(settings.get("palace_path") or ""),
        memory_port=int(settings.get("memory_port") or 0),
        consolidator=ConsolidatorConfig(**(settings.get("consolidator") or {})),
        created_at=str(settings.get("created_at") or _dt_to_iso(row.created_at) or _now_iso()),
    )


def _agent_from_companion(row: CompanionRow | None) -> AgentMetadataRecord | None:
    if row is None:
        return None
    registry = (row.metadata_json or {}).get("registry") or {}
    user_id = registry.get("user_id") or row.owner_id
    template_id = registry.get("template_id")
    if not template_id:
        return None
    return AgentMetadataRecord(
        agent_id=row.companion_id,
        tenant_id=registry.get("tenant_id", "default"),
        user_id=user_id,
        template_id=template_id,
        template_revision=int(registry.get("template_revision") or 1),
        display_name=row.display_name,
        created_at=str(registry.get("created_at") or _dt_to_iso(row.created_at) or _now_iso()),
    )


def _binding_from_device(row: DeviceRow) -> DeviceBindingRecord:
    policy = row.access_policy_json or {}
    return DeviceBindingRecord(
        device_id=row.device_id,
        agent_id=str(row.bound_companion_id),
        bound_at=str(policy.get("bound_at") or _dt_to_iso(row.updated_at) or _now_iso()),
        interaction_mode=row.interaction_mode,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dt_to_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: object) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))
