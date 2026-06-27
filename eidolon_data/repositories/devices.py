"""Device repositories."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select

from eidolon_data.db.base import utc_now
from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import DeviceRow


class DevicesRepository(Repository):
    async def create_device(
        self,
        *,
        device_id: str,
        owner_id: str,
        name: str = "",
        kind: str = "unknown",
        status: str = "active",
        approved_at: datetime | None = None,
        approved_by: str | None = None,
        bound_companion_id: str | None = None,
        interaction_mode: str | None = None,
        auth_type: str | None = None,
        secret_ref: str | None = None,
        capabilities_json: dict | None = None,
        network_json: dict | None = None,
        access_policy_json: dict | None = None,
        metadata_json: dict | None = None,
        last_seen_at: datetime | None = None,
        revoked_at: datetime | None = None,
    ) -> DeviceRow:
        row = DeviceRow(
            device_id=device_id,
            owner_id=owner_id,
            name=name,
            kind=kind,
            status=status,
            approved_at=approved_at,
            approved_by=approved_by,
            bound_companion_id=bound_companion_id,
            interaction_mode=interaction_mode,
            auth_type=auth_type,
            secret_ref=secret_ref,
            capabilities_json=capabilities_json or {},
            network_json=network_json or {},
            access_policy_json=access_policy_json or {},
            metadata_json=metadata_json or {},
            last_seen_at=last_seen_at,
            revoked_at=revoked_at,
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def put_device(
        self,
        *,
        device_id: str,
        owner_id: str,
        name: str = "",
        kind: str = "unknown",
        status: str = "active",
        approved_at: datetime | None = None,
        approved_by: str | None = None,
        bound_companion_id: str | None = None,
        interaction_mode: str | None = None,
        auth_type: str | None = None,
        secret_ref: str | None = None,
        capabilities_json: dict | None = None,
        network_json: dict | None = None,
        access_policy_json: dict | None = None,
        metadata_json: dict | None = None,
        last_seen_at: datetime | None = None,
        revoked_at: datetime | None = None,
    ) -> DeviceRow:
        async with self._session_factory() as session:
            row = await session.get(DeviceRow, device_id)
            if row is None:
                row = DeviceRow(
                    device_id=device_id,
                    owner_id=owner_id,
                    name=name,
                    kind=kind,
                    status=status,
                    approved_at=approved_at,
                    approved_by=approved_by,
                    bound_companion_id=bound_companion_id,
                    interaction_mode=interaction_mode,
                    auth_type=auth_type,
                    secret_ref=secret_ref,
                    capabilities_json=capabilities_json or {},
                    network_json=network_json or {},
                    access_policy_json=access_policy_json or {},
                    metadata_json=metadata_json or {},
                    last_seen_at=last_seen_at,
                    revoked_at=revoked_at,
                )
                session.add(row)
            else:
                row.owner_id = owner_id
                row.name = name
                row.kind = kind
                row.status = status
                row.approved_at = approved_at
                row.approved_by = approved_by
                row.bound_companion_id = bound_companion_id
                row.interaction_mode = interaction_mode
                row.auth_type = auth_type
                row.secret_ref = secret_ref
                row.capabilities_json = capabilities_json or {}
                row.network_json = network_json or {}
                row.access_policy_json = access_policy_json or {}
                row.metadata_json = metadata_json or {}
                row.last_seen_at = last_seen_at
                row.revoked_at = revoked_at
            await session.commit()
            await session.refresh(row)
            return row

    async def get_device(self, device_id: str) -> DeviceRow | None:
        async with self._session_factory() as session:
            return await session.get(DeviceRow, device_id)

    async def list_devices_for_owner(self, owner_id: str) -> list[DeviceRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(DeviceRow).where(DeviceRow.owner_id == owner_id).order_by(DeviceRow.created_at)
            )
            return list(rows)

    async def delete_device(self, device_id: str) -> None:
        async with self._session_factory() as session:
            await session.execute(delete(DeviceRow).where(DeviceRow.device_id == device_id))
            await session.commit()

    async def mark_seen(self, device_id: str, *, at: datetime) -> None:
        async with self._session_factory() as session:
            row = await session.get(DeviceRow, device_id)
            if row is None:
                raise KeyError(f"device not found: {device_id}")
            row.last_seen_at = at
            await session.commit()

    async def update_device(
        self,
        device_id: str,
        *,
        name: str | None = None,
        kind: str | None = None,
        status: str | None = None,
        bound_companion_id: str | None = None,
        interaction_mode: str | None = None,
        auth_type: str | None = None,
        secret_ref: str | None = None,
        capabilities_json: dict | None = None,
        network_json: dict | None = None,
        access_policy_json: dict | None = None,
        metadata_json: dict | None = None,
    ) -> DeviceRow:
        async with self._session_factory() as session:
            row = await session.get(DeviceRow, device_id)
            if row is None:
                raise KeyError(f"device not found: {device_id}")
            if name is not None:
                row.name = name
            if kind is not None:
                row.kind = kind
            if status is not None:
                row.status = status
            if bound_companion_id is not None:
                row.bound_companion_id = bound_companion_id
            if interaction_mode is not None:
                row.interaction_mode = interaction_mode
            if auth_type is not None:
                row.auth_type = auth_type
            if secret_ref is not None:
                row.secret_ref = secret_ref
            if capabilities_json is not None:
                row.capabilities_json = capabilities_json
            if network_json is not None:
                row.network_json = network_json
            if access_policy_json is not None:
                row.access_policy_json = access_policy_json
            if metadata_json is not None:
                row.metadata_json = metadata_json
            row.updated_at = utc_now()
            await session.commit()
            await session.refresh(row)
            return row

    async def approve(self, device_id: str, *, actor_id: str | None = None) -> DeviceRow:
        async with self._session_factory() as session:
            row = await session.get(DeviceRow, device_id)
            if row is None:
                raise KeyError(f"device not found: {device_id}")
            row.status = "approved"
            row.approved_at = utc_now()
            row.approved_by = actor_id or "admin"
            row.revoked_at = None
            row.updated_at = utc_now()
            await session.commit()
            await session.refresh(row)
            return row

    async def revoke(self, device_id: str) -> DeviceRow:
        async with self._session_factory() as session:
            row = await session.get(DeviceRow, device_id)
            if row is None:
                raise KeyError(f"device not found: {device_id}")
            row.status = "revoked"
            row.revoked_at = utc_now()
            row.updated_at = utc_now()
            await session.commit()
            await session.refresh(row)
            return row

    async def bind_companion(self, device_id: str, *, companion_id: str | None) -> DeviceRow:
        async with self._session_factory() as session:
            row = await session.get(DeviceRow, device_id)
            if row is None:
                raise KeyError(f"device not found: {device_id}")
            row.bound_companion_id = companion_id
            row.updated_at = utc_now()
            await session.commit()
            await session.refresh(row)
            return row
