"""Hub DeviceStore adapter backed by Eidolon Data."""

from __future__ import annotations

from datetime import datetime, timezone

from eidolon_sdk.biz.registry.models import DeviceRegistryRecord

from eidolon_data.services.datastore import DataStore
from eidolon_data.services.owner_workspace import WEB_BODY_KIND


def _is_hub_managed(row) -> bool:
    """Whether a sovereign device row belongs in Hub's device registry.

    The sovereign device table holds *every* body — physical devices plus the
    virtual web bodies that owner onboarding provisions. Hub's registry only
    governs physical devices (discovery / approval / LiveKit reachability), so
    this adapter — the sole boundary between the two worlds — filters web bodies
    out here. Everything downstream (DeviceManager cache, admin hardware table,
    presence probes) then stays free of any web-body special-casing. Web bodies'
    lifecycle is owned by the owner-scoped "本机 Web 身体" card instead.
    """
    return row.kind != WEB_BODY_KIND


class EidolonDataDeviceRegistryRepository:
    """Implement Hub's DeviceStore protocol over the new sovereign schema."""

    def __init__(self, store: DataStore, *, owner_id: str | None = None) -> None:
        self._store = store
        self._owner_id = owner_id
        self._schema_ready = False

    async def get(self, device_id: str) -> DeviceRegistryRecord | None:
        await self._ensure_ready()
        row = await self._store.devices.get_device(device_id)
        if row is None or not _is_hub_managed(row):
            return None
        return await self._record_from_row(row)

    async def put(self, record: DeviceRegistryRecord) -> None:
        await self._ensure_ready()
        existing = await self._store.devices.get_device(record.device_id)
        metadata = _without_hub_metadata(existing.metadata_json or {}) if existing else {}
        metadata.update(record.metadata or {})
        metadata["hub_registry"] = {
            "paired": record.paired,
            "approved": record.approved,
            "approved_at": record.approved_at,
            "created_at": record.created_at or _now_iso(),
            "last_seen": record.last_seen or _now_iso(),
        }
        auth_type = "psk" if record.psk_hash else None
        secret_ref = f"psk_hash:{record.psk_hash}" if record.psk_hash else None
        await self._store.devices.put_device(
            device_id=record.device_id,
            owner_id=existing.owner_id if existing else self._owner_id,
            name=record.name,
            kind=record.kind,
            status=_status_from_record(record, existing),
            approved_at=existing.approved_at if existing else None,
            approved_by=existing.approved_by if existing else None,
            bound_companion_id=existing.bound_companion_id if existing else None,
            interaction_mode=existing.interaction_mode if existing else None,
            auth_type=auth_type,
            secret_ref=secret_ref,
            capabilities_json=(
                {"capabilities": list(record.capabilities)}
                if record.capabilities
                else (dict(existing.capabilities_json or {}) if existing else None)
            ),
            access_policy_json=dict(existing.access_policy_json or {}) if existing else None,
            metadata_json=metadata,
            last_seen_at=_parse_dt(record.last_seen),
            revoked_at=existing.revoked_at if existing else None,
        )

    async def delete(self, device_id: str) -> None:
        await self._ensure_ready()
        await self._store.devices.delete_device(device_id)

    async def list_all(self) -> dict[str, DeviceRegistryRecord]:
        await self._ensure_ready()
        rows = await self._store.devices.list_all_devices()
        records = [
            await self._record_from_row(row) for row in rows if _is_hub_managed(row)
        ]
        return {record.device_id: record for record in records}

    async def _ensure_ready(self) -> None:
        if self._schema_ready:
            return
        await self._store.init_schema()
        self._schema_ready = True

    async def _record_from_row(self, row) -> DeviceRegistryRecord:
        # Only reached for Hub-managed rows (see _is_hub_managed), which always
        # carry a hub_registry block written by Hub's register/approve path.
        hub = dict((row.metadata_json or {}).get("hub_registry") or {})
        psk_hash = None
        if row.auth_type == "psk" and row.secret_ref and row.secret_ref.startswith("psk_hash:"):
            psk_hash = row.secret_ref.removeprefix("psk_hash:")
        return DeviceRegistryRecord(
            device_id=row.device_id,
            name=row.name,
            kind=row.kind,
            enabled=row.status not in {"disabled", "revoked"},
            psk_hash=psk_hash,
            paired=bool(hub.get("paired") or psk_hash),
            approved=bool(hub.get("approved")),
            approved_at=hub.get("approved_at"),
            created_at=str(hub.get("created_at") or _dt_to_iso(row.created_at) or _now_iso()),
            last_seen=str(hub.get("last_seen") or _dt_to_iso(row.last_seen_at) or _now_iso()),
            metadata=_without_hub_metadata(row.metadata_json or {}),
        )


def _without_hub_metadata(metadata: dict) -> dict:
    data = dict(metadata)
    data.pop("hub_registry", None)
    return data


def _status_from_record(record: DeviceRegistryRecord, existing) -> str:
    if existing is not None and existing.status == "revoked":
        return "revoked"
    if not record.enabled:
        return "disabled"
    if existing is not None and existing.owner_id is not None:
        return "active"
    return "discovered"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _dt_to_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
