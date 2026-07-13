"""Persistence and invariants for the ATK Guard control plane."""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from eidolon_data.db.base import utc_now
from eidolon_data.repositories.base import Repository
from eidolon_data.repositories.guard_runtime_deliveries import enqueue_runtime_delivery
from eidolon_data.schema.models import CompanionRow, DeviceRow, GuardBindingRow, OwnerRow
from eidolon_sdk.biz.guard import normalize_guard_policy_config, normalize_guard_runtime_config

GUARD_COMPANION_KIND = "guard"
ACTIVE_GUARD_BINDING_STATE = "active"
_TERMINAL_STATES = {"disabled", "revoked", "replaced"}
GUARD_RUNTIME_DESIRED_RUNNING = "running"
GUARD_RUNTIME_DESIRED_STOPPED = "stopped"


def is_guard_capable(capabilities: object) -> bool:
    """Accept the canonical declaration and the early manifest variants.

    Capability declarations are self-reported facts only.  This predicate is
    used when an administrator explicitly claims a device; it never assigns an
    owner by itself.
    """
    if not isinstance(capabilities, dict):
        return False
    guard = capabilities.get("guard")
    if guard is True:
        return True
    if isinstance(guard, dict) and bool(guard.get("enabled", True)):
        return True
    if bool(capabilities.get("guard_capable")):
        return True
    manifest = capabilities.get("manifest") or capabilities.get("ops") or []
    if not isinstance(manifest, list):
        return False
    for item in manifest:
        if isinstance(item, str) and (item == "guard" or item.startswith("guard.")):
            return True
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("op") or item.get("kind") or "")
            if name == "guard" or name.startswith("guard."):
                return True
    return False


class GuardBindingsRepository(Repository):
    async def list_pending_guard_devices(self) -> list[DeviceRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(DeviceRow)
                .where(DeviceRow.owner_id.is_(None))
                .order_by(DeviceRow.last_seen_at.desc().nullslast(), DeviceRow.created_at.desc())
            )
            return [row for row in rows if is_guard_capable(row.capabilities_json)]

    async def get(self, binding_id: str) -> GuardBindingRow | None:
        async with self._session_factory() as session:
            return await session.get(GuardBindingRow, binding_id)

    async def get_active_for_owner(self, owner_id: str) -> GuardBindingRow | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(GuardBindingRow)
                .where(
                    GuardBindingRow.owner_id == owner_id,
                    GuardBindingRow.state == ACTIVE_GUARD_BINDING_STATE,
                )
                .order_by(GuardBindingRow.activated_at.desc())
            )

    async def get_active_for_device(self, device_id: str) -> GuardBindingRow | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(GuardBindingRow).where(
                    GuardBindingRow.device_id == device_id,
                    GuardBindingRow.state == ACTIVE_GUARD_BINDING_STATE,
                )
            )

    async def list_for_owner(self, owner_id: str) -> list[GuardBindingRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(GuardBindingRow)
                .where(GuardBindingRow.owner_id == owner_id)
                .order_by(GuardBindingRow.created_at.desc())
            )
            return list(rows)

    async def ensure_guard_companion(
        self,
        *,
        owner_id: str,
        companion_id: str,
        display_name: str = "ATK Guard",
    ) -> CompanionRow:
        async with self._session_factory() as session:
            if await session.get(OwnerRow, owner_id) is None:
                raise KeyError(f"owner not found: {owner_id}")
            row = await session.get(CompanionRow, companion_id)
            if row is None:
                row = _new_guard_companion(
                    owner_id=owner_id,
                    companion_id=companion_id,
                    display_name=display_name,
                )
                session.add(row)
            else:
                _validate_guard_companion(row, owner_id)
            await session.commit()
            await session.refresh(row)
            return row

    async def claim(
        self,
        *,
        owner_id: str,
        device_id: str,
        guard_companion_id: str,
        guard_display_name: str = "ATK Guard",
        policy_id: str = "silent_presence",
        config_json: dict | None = None,
        runtime_config_json: dict | None = None,
        replace: bool = False,
    ) -> GuardBindingRow:
        """Explicitly bind a pending guard device; discovery never calls this."""
        normalized_config = normalize_guard_policy_config(policy_id, config_json)
        normalized_runtime_config = normalize_guard_runtime_config(runtime_config_json)
        async with self._session_factory() as session:
            companion = await session.get(CompanionRow, guard_companion_id)
            if companion is None:
                if await session.get(OwnerRow, owner_id) is None:
                    raise KeyError(f"owner not found: {owner_id}")
                companion = _new_guard_companion(
                    owner_id=owner_id,
                    companion_id=guard_companion_id,
                    display_name=guard_display_name,
                )
                session.add(companion)
            _validate_guard_companion(companion, owner_id)
            device = await session.get(DeviceRow, device_id)
            if device is None:
                raise KeyError(f"device not found: {device_id}")
            if not is_guard_capable(device.capabilities_json):
                raise ValueError(f"device {device_id!r} is not guard-capable")
            if device.owner_id is not None and device.owner_id != owner_id:
                raise ValueError(f"device {device_id!r} already belongs to owner {device.owner_id!r}")

            current = await session.scalar(
                select(GuardBindingRow).where(
                    GuardBindingRow.owner_id == owner_id,
                    GuardBindingRow.state == ACTIVE_GUARD_BINDING_STATE,
                )
            )
            device_current = await session.scalar(
                select(GuardBindingRow).where(
                    GuardBindingRow.device_id == device_id,
                    GuardBindingRow.state == ACTIVE_GUARD_BINDING_STATE,
                )
            )
            if current is not None and current.device_id == device_id:
                return current
            if current is not None and not replace:
                raise ValueError("owner already has an active guard; set replace=true to replace it")
            if device_current is not None and device_current.owner_id != owner_id:
                raise ValueError(f"device {device_id!r} already has an active guard binding")

            now = utc_now()
            revision = 1
            if current is not None:
                current.state = "replaced"
                current.disabled_at = now
                current.desired_runtime_state = GUARD_RUNTIME_DESIRED_STOPPED
                current.runtime_revision += 1
                current.updated_at = now
                enqueue_runtime_delivery(session, current)
                revision = current.config_revision + 1
                old_device = await session.get(DeviceRow, current.device_id)
                if old_device is not None:
                    old_device.owner_id = None
                    old_device.bound_companion_id = None
                    old_device.interaction_mode = None
                    old_device.status = "discovered"
                    old_device.updated_at = now

            device.owner_id = owner_id
            device.bound_companion_id = guard_companion_id
            device.status = "active"
            device.approved_at = device.approved_at or now
            device.approved_by = device.approved_by or "admin:guard-claim"
            device.revoked_at = None
            device.updated_at = now
            row = GuardBindingRow(
                binding_id=f"gb_{uuid4().hex}",
                owner_id=owner_id,
                guard_companion_id=guard_companion_id,
                device_id=device_id,
                state=ACTIVE_GUARD_BINDING_STATE,
                policy_id=policy_id,
                config_revision=revision,
                config_json=normalized_config,
                runtime_revision=1,
                runtime_config_json=normalized_runtime_config,
                desired_runtime_state=GUARD_RUNTIME_DESIRED_RUNNING,
                status_json={"subscriber": "mission_control_fixture"},
                activated_at=now,
            )
            session.add(row)
            enqueue_runtime_delivery(session, row)
            try:
                await session.commit()
            except IntegrityError as exc:
                await session.rollback()
                raise ValueError("guard binding changed concurrently; retry") from exc
            await session.refresh(row)
            return row

    async def update_runtime_config(
        self,
        *,
        binding_id: str,
        runtime_config_json: dict | None,
        expected_revision: int,
    ) -> GuardBindingRow:
        """Replace local runtime parameters with a separate optimistic revision."""
        if expected_revision < 1:
            raise ValueError("expected guard runtime revision must be positive")
        normalized_runtime_config = normalize_guard_runtime_config(runtime_config_json)
        async with self._session_factory() as session:
            row = await session.get(GuardBindingRow, binding_id)
            if row is None:
                raise KeyError(f"guard binding not found: {binding_id}")
            if row.state != ACTIVE_GUARD_BINDING_STATE:
                raise ValueError("guard binding is not active")
            result = await session.execute(
                update(GuardBindingRow)
                .where(
                    GuardBindingRow.binding_id == binding_id,
                    GuardBindingRow.state == ACTIVE_GUARD_BINDING_STATE,
                    GuardBindingRow.runtime_revision == expected_revision,
                )
                .values(
                    runtime_config_json=normalized_runtime_config,
                    runtime_revision=expected_revision + 1,
                    updated_at=utc_now(),
                )
            )
            if result.rowcount != 1:
                await session.rollback()
                raise ValueError("guard runtime configuration changed; refresh and retry")
            await session.refresh(row)
            enqueue_runtime_delivery(session, row)
            await session.commit()
            await session.refresh(row)
            return row

    async def update_config(
        self,
        *,
        binding_id: str,
        config_json: dict | None,
        expected_revision: int,
    ) -> GuardBindingRow:
        """Replace the active policy config with optimistic concurrency control."""
        if expected_revision < 1:
            raise ValueError("expected guard config revision must be positive")
        async with self._session_factory() as session:
            row = await session.get(GuardBindingRow, binding_id)
            if row is None:
                raise KeyError(f"guard binding not found: {binding_id}")
            if row.state != ACTIVE_GUARD_BINDING_STATE:
                raise ValueError("guard binding is not active")
            normalized_config = normalize_guard_policy_config(row.policy_id, config_json)
            result = await session.execute(
                update(GuardBindingRow)
                .where(
                    GuardBindingRow.binding_id == binding_id,
                    GuardBindingRow.state == ACTIVE_GUARD_BINDING_STATE,
                    GuardBindingRow.config_revision == expected_revision,
                )
                .values(
                    config_json=normalized_config,
                    config_revision=expected_revision + 1,
                    updated_at=utc_now(),
                )
            )
            if result.rowcount != 1:
                await session.rollback()
                raise ValueError("guard binding configuration changed; refresh and retry")
            await session.commit()
            await session.refresh(row)
            return row

    async def disable(self, binding_id: str, *, revoke: bool = False) -> GuardBindingRow:
        async with self._session_factory() as session:
            row = await session.get(GuardBindingRow, binding_id)
            if row is None:
                raise KeyError(f"guard binding not found: {binding_id}")
            if row.state not in _TERMINAL_STATES:
                now = utc_now()
                row.state = "revoked" if revoke else "disabled"
                row.disabled_at = now
                row.revoked_at = now if revoke else None
                row.desired_runtime_state = GUARD_RUNTIME_DESIRED_STOPPED
                row.runtime_revision += 1
                row.updated_at = now
                enqueue_runtime_delivery(session, row)
                device = await session.get(DeviceRow, row.device_id)
                if device is not None:
                    device.owner_id = None
                    device.bound_companion_id = None
                    device.interaction_mode = None
                    device.status = "revoked" if revoke else "discovered"
                    device.revoked_at = now if revoke else None
                    device.updated_at = now
            await session.commit()
            await session.refresh(row)
            return row


def _validate_guard_companion(companion: CompanionRow, owner_id: str) -> None:
    if companion.owner_id != owner_id:
        raise ValueError(f"guard companion {companion.companion_id!r} belongs to another owner")
    if companion.kind != GUARD_COMPANION_KIND:
        raise ValueError(f"companion {companion.companion_id!r} is not a guard")
    if companion.status != "active" or companion.is_master:
        raise ValueError(f"guard companion {companion.companion_id!r} is not an active non-master guard")
    if companion.current_genome_id or companion.default_memory_realm_id:
        raise ValueError("guard companions cannot own persona genomes or memory realms")


def _new_guard_companion(*, owner_id: str, companion_id: str, display_name: str) -> CompanionRow:
    return CompanionRow(
        companion_id=companion_id,
        owner_id=owner_id,
        display_name=display_name,
        kind=GUARD_COMPANION_KIND,
        status="active",
        is_master=False,
        companion_type="slave",
        # Guard companions do not own persona, agent, or memory state.
        current_genome_id=None,
        default_memory_realm_id=None,
        profile_json={},
        runtime_config_json={},
        metadata_json={"guard_control_plane": True},
    )
