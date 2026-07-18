"""Persistence and invariants for the ATK Guard control plane."""

from __future__ import annotations

from dataclasses import dataclass, replace as dataclass_replace
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

from eidolon_sdk.biz.guard import normalize_guard_policy_config, normalize_guard_runtime_config
from eidolon_sdk.biz.persona import (
    PERSONA_GENOME_SCHEMA,
    PERSONA_REALIZER,
    build_default_persona_genome,
    persona_genome_hash,
    persona_genome_to_json,
)
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from eidolon_data.db.base import utc_now
from eidolon_data.repositories.base import Repository
from eidolon_data.repositories.guard_runtime_deliveries import enqueue_runtime_delivery
from eidolon_data.repositories.owner_face_profiles import enqueue_desired_profile_for_binding
from eidolon_data.schema.models import (
    CompanionRow,
    DeviceRow,
    GuardBindingRow,
    MemoryRealmRow,
    OwnerRow,
    PersonaGenomeRow,
)

GUARD_COMPANION_KIND = "guard"
GUARD_COMPANION_TYPE = "guard"
ACTIVE_GUARD_BINDING_STATE = "active"
_TERMINAL_STATES = {"disabled", "revoked", "replaced"}
GUARD_RUNTIME_DESIRED_RUNNING = "running"
GUARD_RUNTIME_DESIRED_STOPPED = "stopped"


@dataclass(frozen=True)
class GuardOwnerPresenceProjection:
    binding_id: str
    state: str
    profile_revision: int | None
    correlation_id: str | None
    guard_epoch: int
    sequence: int
    observed_at: datetime | None
    expires_at: datetime | None
    transition: str


def _presence_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _presence_projection(
    binding_id: str,
    payload: object,
    *,
    now: datetime,
    transition: str = "unchanged",
) -> GuardOwnerPresenceProjection:
    value = payload if isinstance(payload, dict) else {}
    expires_at = _presence_datetime(value.get("expires_at"))
    active = value.get("state") == "present" and expires_at is not None and expires_at > now
    return GuardOwnerPresenceProjection(
        binding_id=binding_id,
        state="present" if active else "absent",
        profile_revision=(
            int(value["profile_revision"])
            if isinstance(value.get("profile_revision"), int)
            else None
        ),
        correlation_id=(
            value["correlation_id"]
            if isinstance(value.get("correlation_id"), str)
            else None
        ),
        guard_epoch=int(value.get("guard_epoch") or 0),
        sequence=int(value.get("sequence") or 0),
        observed_at=_presence_datetime(value.get("observed_at")),
        expires_at=expires_at if active else None,
        transition=transition,
    )


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
    async def get_owner_presence(
        self, binding_id: str, *, now: datetime | None = None
    ) -> GuardOwnerPresenceProjection | None:
        effective_now = now or utc_now()
        async with self._session_factory() as session:
            row = await session.get(GuardBindingRow, binding_id)
            if row is None:
                return None
            return _presence_projection(
                binding_id,
                (row.status_json or {}).get("owner_presence"),
                now=effective_now,
            )

    async def apply_owner_presence(
        self,
        *,
        binding_id: str,
        state: str,
        profile_revision: int,
        correlation_id: str,
        guard_epoch: int,
        sequence: int,
        lease_ms: int,
        now: datetime | None = None,
    ) -> GuardOwnerPresenceProjection | None:
        if state not in {"present", "absent"}:
            raise ValueError("owner presence state must be present or absent")
        if profile_revision < 1 or guard_epoch < 0 or sequence < 1:
            raise ValueError("owner presence revision and ordering must be positive")
        if (state == "present" and lease_ms < 5_000) or (
            state == "absent" and lease_ms != 0
        ):
            raise ValueError("owner presence lease does not match state")
        effective_now = now or utc_now()
        for _ in range(3):
            async with self._session_factory() as session:
                row = await session.get(GuardBindingRow, binding_id)
                if row is None or row.state != ACTIVE_GUARD_BINDING_STATE:
                    return None
                status = dict(row.status_json or {})
                current = _presence_projection(
                    binding_id,
                    status.get("owner_presence"),
                    now=effective_now,
                )
                same_epoch = (
                    current.correlation_id == correlation_id
                    and current.guard_epoch == guard_epoch
                )
                if same_epoch and sequence <= current.sequence:
                    return dataclass_replace(current, transition="stale")
                if state == "absent" and current.state == "present" and not same_epoch:
                    return dataclass_replace(current, transition="stale")
                transition = (
                    "entered"
                    if state == "present" and current.state != "present"
                    else "renewed"
                    if state == "present"
                    else "left"
                    if current.state == "present"
                    else "unchanged"
                )
                expires_at = (
                    effective_now + timedelta(milliseconds=lease_ms)
                    if state == "present"
                    else None
                )
                presence = {
                    "state": state,
                    "profile_revision": profile_revision,
                    "correlation_id": correlation_id,
                    "guard_epoch": guard_epoch,
                    "sequence": sequence,
                    "observed_at": effective_now.isoformat(),
                    "expires_at": expires_at.isoformat() if expires_at else None,
                }
                status["owner_presence"] = presence
                previous_updated_at = row.updated_at
                changed = await session.execute(
                    update(GuardBindingRow)
                    .where(
                        GuardBindingRow.binding_id == binding_id,
                        GuardBindingRow.updated_at == previous_updated_at,
                    )
                    .values(status_json=status, updated_at=effective_now)
                )
                if changed.rowcount == 1:
                    await session.commit()
                    return _presence_projection(
                        binding_id,
                        presence,
                        now=effective_now,
                        transition=transition,
                    )
                await session.rollback()
        raise RuntimeError("guard owner presence changed concurrently; retry")

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
        """Compatibility lookup for callers that only need one active Guard.

        Owners may now have multiple active Guards.  New code should use
        ``list_for_owner`` and select deliberately; this method returns the
        most recently activated binding for legacy read-only callers.
        """
        async with self._session_factory() as session:
            return await session.scalar(
                select(GuardBindingRow)
                .where(
                    GuardBindingRow.owner_id == owner_id,
                    GuardBindingRow.state == ACTIVE_GUARD_BINDING_STATE,
                )
                .order_by(GuardBindingRow.activated_at.desc())
                .limit(1)
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
            await _ensure_guard_workspace(session, row)
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
            await _ensure_guard_workspace(session, companion)
            device = await session.get(DeviceRow, device_id)
            if device is None:
                raise KeyError(f"device not found: {device_id}")
            if not is_guard_capable(device.capabilities_json):
                raise ValueError(f"device {device_id!r} is not guard-capable")
            if device.owner_id is not None and device.owner_id != owner_id:
                raise ValueError(f"device {device_id!r} already belongs to owner {device.owner_id!r}")

            companion_current = await session.scalar(
                select(GuardBindingRow).where(
                    GuardBindingRow.guard_companion_id == guard_companion_id,
                    GuardBindingRow.state == ACTIVE_GUARD_BINDING_STATE,
                )
            )
            device_current = await session.scalar(
                select(GuardBindingRow).where(
                    GuardBindingRow.device_id == device_id,
                    GuardBindingRow.state == ACTIVE_GUARD_BINDING_STATE,
                )
            )
            existing_pair = await session.scalar(
                select(GuardBindingRow)
                .where(
                    GuardBindingRow.owner_id == owner_id,
                    GuardBindingRow.device_id == device_id,
                )
                .order_by(GuardBindingRow.updated_at.desc())
            )
            if companion_current is not None and companion_current.device_id == device_id:
                return companion_current
            if companion_current is not None and not replace:
                raise ValueError(
                    "guard companion already has an active device; set replace=true to replace it"
                )
            if device_current is not None and device_current.owner_id != owner_id:
                raise ValueError(f"device {device_id!r} already has an active guard binding")
            if (
                device_current is not None
                and device_current.guard_companion_id != guard_companion_id
            ):
                raise ValueError(
                    f"device {device_id!r} is already bound to guard companion "
                    f"{device_current.guard_companion_id!r}"
                )

            now = utc_now()
            next_config_revision = 1
            if companion_current is not None:
                companion_current.state = "replaced"
                companion_current.disabled_at = now
                companion_current.desired_runtime_state = GUARD_RUNTIME_DESIRED_STOPPED
                companion_current.runtime_revision += 1
                companion_current.updated_at = now
                enqueue_runtime_delivery(session, companion_current)
                next_config_revision = companion_current.config_revision + 1
                old_device = await session.get(DeviceRow, companion_current.device_id)
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
            if existing_pair is None:
                row = GuardBindingRow(
                    binding_id=f"gb_{uuid4().hex}",
                    owner_id=owner_id,
                    guard_companion_id=guard_companion_id,
                    device_id=device_id,
                    state=ACTIVE_GUARD_BINDING_STATE,
                    policy_id=policy_id,
                    config_revision=next_config_revision,
                    config_json=normalized_config,
                    runtime_revision=1,
                    runtime_config_json=normalized_runtime_config,
                    desired_runtime_state=GUARD_RUNTIME_DESIRED_RUNNING,
                    status_json={"subscriber": "mission_control_fixture"},
                    activated_at=now,
                )
                session.add(row)
            else:
                row = existing_pair
                row.guard_companion_id = guard_companion_id
                row.state = ACTIVE_GUARD_BINDING_STATE
                row.policy_id = policy_id
                row.config_revision = max(
                    row.config_revision + 1,
                    next_config_revision,
                )
                row.config_json = normalized_config
                row.runtime_revision += 1
                row.runtime_config_json = normalized_runtime_config
                row.desired_runtime_state = GUARD_RUNTIME_DESIRED_RUNNING
                row.status_json = {"subscriber": "mission_control_fixture"}
                row.activated_at = now
                row.disabled_at = None
                row.revoked_at = None
                row.updated_at = now
            enqueue_runtime_delivery(session, row)
            await enqueue_desired_profile_for_binding(session, row)
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
    if companion.companion_type not in {GUARD_COMPANION_TYPE, "slave"}:
        raise ValueError(f"guard companion {companion.companion_id!r} has an invalid companion type")


def _new_guard_companion(*, owner_id: str, companion_id: str, display_name: str) -> CompanionRow:
    return CompanionRow(
        companion_id=companion_id,
        owner_id=owner_id,
        display_name=display_name,
        kind=GUARD_COMPANION_KIND,
        status="active",
        is_master=False,
        companion_type=GUARD_COMPANION_TYPE,
        # Guard companions are non-conversational, but still receive a complete
        # workspace so generic companion-scoped services can resolve safely.
        current_genome_id=None,
        default_memory_realm_id=None,
        profile_json={},
        runtime_config_json={},
        metadata_json={"guard_control_plane": True, "runtime_role": "guard"},
    )


def _guard_workspace_ids(companion_id: str) -> tuple[str, str]:
    """Stable, bounded identifiers for a Guard's compatibility workspace."""
    digest = sha256(companion_id.encode("utf-8")).hexdigest()[:40]
    return f"g_guard_{digest}", f"r_guard_{digest}"


async def _ensure_guard_workspace(session, companion: CompanionRow) -> None:
    """Provide the standard companion prerequisites without making Guard a body.

    Several generic read paths intentionally require a committed genome and an
    active default memory realm.  Guard's own runtime never consumes either,
    but provisioning them atomically with its identity prevents those generic
    paths from failing and keeps each Guard isolated from the owner's primary
    companion workspace.
    """
    companion.companion_type = GUARD_COMPANION_TYPE
    companion.metadata_json = {
        **(companion.metadata_json or {}),
        "guard_control_plane": True,
        "runtime_role": "guard",
        "workspace_mode": "compatibility",
    }
    genome_id, realm_id = _guard_workspace_ids(companion.companion_id)

    genome = None
    if companion.current_genome_id:
        genome = await session.get(PersonaGenomeRow, companion.current_genome_id)
        if genome is not None and genome.companion_id != companion.companion_id:
            raise ValueError("guard companion genome belongs to another companion")
    if genome is None:
        genome = await session.scalar(
            select(PersonaGenomeRow)
            .where(
                PersonaGenomeRow.companion_id == companion.companion_id,
                PersonaGenomeRow.status == "committed",
            )
            .order_by(PersonaGenomeRow.version.desc())
        )
    if genome is None:
        existing = await session.get(PersonaGenomeRow, genome_id)
        if existing is not None and existing.companion_id != companion.companion_id:
            raise ValueError("generated guard genome id is already in use")
        if existing is None:
            normalized = persona_genome_to_json(
                build_default_persona_genome(
                    name=companion.display_name or companion.companion_id,
                    archetype="companion",
                    origin="guard_workspace_auto_provision",
                )
            )
            normalized["provenance"] = {
                **dict(normalized.get("provenance") or {}),
                "owner_id": companion.owner_id,
                "companion_id": companion.companion_id,
                "runtime_role": "guard",
            }
            existing = PersonaGenomeRow(
                genome_id=genome_id,
                companion_id=companion.companion_id,
                version=1,
                status="committed",
                schema_version=PERSONA_GENOME_SCHEMA,
                genome_hash=persona_genome_hash(normalized),
                realizer_version=PERSONA_REALIZER,
                source_json={"source_type": "guard_workspace_auto_provision"},
                genome_json=normalized,
                change_summary="Guard compatibility workspace genome",
            )
            session.add(existing)
        genome = existing
    companion.current_genome_id = genome.genome_id

    realm = None
    if companion.default_memory_realm_id:
        realm = await session.get(MemoryRealmRow, companion.default_memory_realm_id)
        if realm is not None and (
            realm.companion_id != companion.companion_id or realm.owner_id != companion.owner_id
        ):
            raise ValueError("guard companion memory realm belongs outside its owner")
    if realm is None:
        realm = await session.scalar(
            select(MemoryRealmRow)
            .where(
                MemoryRealmRow.companion_id == companion.companion_id,
                MemoryRealmRow.owner_id == companion.owner_id,
                MemoryRealmRow.status == "active",
            )
            .order_by(MemoryRealmRow.created_at.desc())
        )
    if realm is None:
        existing = await session.get(MemoryRealmRow, realm_id)
        if existing is not None and (
            existing.companion_id != companion.companion_id or existing.owner_id != companion.owner_id
        ):
            raise ValueError("generated guard memory realm id is already in use")
        if existing is None:
            existing = MemoryRealmRow(
                realm_id=realm_id,
                owner_id=companion.owner_id,
                companion_id=companion.companion_id,
                engine="mempalace",
                policy_json={"scope": "guard", "recall": "guard_isolated"},
                status="active",
            )
            session.add(existing)
        realm = existing
    companion.default_memory_realm_id = realm.realm_id
