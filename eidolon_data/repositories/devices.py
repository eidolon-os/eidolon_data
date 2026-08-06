"""Device repositories."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select

from eidolon_data.db.base import utc_now
from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import CompanionRow, DeviceRow


class DevicesRepository(Repository):
    async def create_device(
        self,
        *,
        device_id: str,
        owner_id: str | None,
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
            await _validate_bound_companion(
                session,
                owner_id=owner_id,
                companion_id=bound_companion_id,
                current_device_id=device_id,
            )
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
            await session.commit()
            await session.refresh(row)
            return row

    async def put_device(
        self,
        *,
        device_id: str,
        owner_id: str | None,
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
            await _validate_bound_companion(
                session,
                owner_id=owner_id,
                companion_id=bound_companion_id,
                current_device_id=device_id,
            )
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

    async def list_devices_for_companion(self, companion_id: str) -> list[DeviceRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(DeviceRow)
                .where(DeviceRow.bound_companion_id == companion_id)
                .order_by(DeviceRow.created_at)
            )
            return list(rows)

    async def list_unclaimed_devices(self) -> list[DeviceRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(DeviceRow)
                .where(DeviceRow.owner_id.is_(None))
                .order_by(DeviceRow.last_seen_at.desc().nullslast(), DeviceRow.created_at.desc())
            )
            return list(rows)

    async def list_all_devices(self) -> list[DeviceRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(select(DeviceRow).order_by(DeviceRow.created_at))
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
            if bound_companion_id is not None:
                await _validate_bound_companion(
                    session,
                    owner_id=row.owner_id,
                    companion_id=bound_companion_id,
                    current_device_id=device_id,
                )
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

    async def approve(self, device_id: str) -> DeviceRow:
        async with self._session_factory() as session:
            row = await session.get(DeviceRow, device_id)
            if row is None:
                raise KeyError(f"device not found: {device_id}")
            row.status = "approved"
            row.approved_at = utc_now()
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

    async def release(self, device_id: str) -> DeviceRow:
        async with self._session_factory() as session:
            row = await session.get(DeviceRow, device_id)
            if row is None:
                raise KeyError(f"device not found: {device_id}")
            row.owner_id = None
            row.bound_companion_id = None
            row.interaction_mode = None
            row.access_policy_json = {}
            row.status = "discovered"
            row.revoked_at = None
            row.updated_at = utc_now()
            await session.commit()
            await session.refresh(row)
            return row

    async def bind_companion(self, device_id: str, *, companion_id: str | None) -> DeviceRow:
        async with self._session_factory() as session:
            row = await session.get(DeviceRow, device_id)
            if row is None:
                raise KeyError(f"device not found: {device_id}")
            await _validate_bound_companion(
                session,
                owner_id=row.owner_id,
                companion_id=companion_id,
                current_device_id=device_id,
            )
            row.bound_companion_id = companion_id
            row.updated_at = utc_now()
            await session.commit()
            await session.refresh(row)
            return row

    async def claim_device(
        self,
        device_id: str,
        *,
        owner_id: str,
        companion_id: str | None,
        approved_by: str = "admin",
        name: str | None = None,
        kind: str | None = None,
        access_policy_json: dict | None = None,
        metadata_json: dict | None = None,
        network_json: dict | None = None,
    ) -> DeviceRow:
        async with self._session_factory() as session:
            row = await session.get(DeviceRow, device_id)
            if row is None:
                raise KeyError(f"device not found: {device_id}")
            if row.owner_id is not None and row.owner_id != owner_id:
                raise ValueError(f"device {device_id!r} already belongs to owner {row.owner_id!r}")
            await _validate_bound_companion(
                session,
                owner_id=owner_id,
                companion_id=companion_id,
                require_binding=True,
                current_device_id=device_id,
            )
            if name is not None:
                row.name = name
            if kind is not None:
                row.kind = kind
            row.owner_id = owner_id
            row.status = "active"
            row.bound_companion_id = companion_id
            # interaction_mode is firmware-declared (sole source of truth); claiming a
            # device never sets an admin override — the column stays as-is (NULL).
            row.approved_at = utc_now()
            row.approved_by = approved_by
            row.revoked_at = None
            if access_policy_json is not None:
                row.access_policy_json = access_policy_json
            if metadata_json is not None:
                row.metadata_json = {**(row.metadata_json or {}), **metadata_json}
            if network_json is not None:
                row.network_json = {**(row.network_json or {}), **network_json}
            row.updated_at = utc_now()
            await session.commit()
            await session.refresh(row)
            return row


async def _validate_bound_companion(
    session,
    *,
    owner_id: str | None,
    companion_id: str | None,
    require_binding: bool = False,
    current_device_id: str | None = None,
) -> None:
    if not companion_id:
        if require_binding:
            raise ValueError("claimed devices must be bound to a companion")
        return
    if not owner_id:
        raise ValueError("bound devices must have an owner_id")
    companion = await session.get(CompanionRow, companion_id)
    if companion is None:
        raise KeyError(f"companion not found: {companion_id}")
    if companion.owner_id != owner_id:
        raise ValueError(
            f"companion {companion_id!r} belongs to owner {companion.owner_id!r}, not {owner_id!r}"
        )
    if companion.status != "active":
        raise ValueError(f"companion {companion_id!r} is not active")
    # A companion may hold multiple bodies (e.g. a local web body + a LAN esp32
    # body). We only validate owner/active here; there is deliberately no
    # one-device-per-companion limit.
