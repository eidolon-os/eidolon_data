"""Domain services for owner-scoped workspace lifecycle."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from eidolon_sdk.biz.persona import (
    PERSONA_GENOME_SCHEMA,
    PERSONA_REALIZER,
    build_default_persona_genome,
    normalize_persona_genome,
    persona_genome_hash,
    persona_genome_to_json,
)

from eidolon_data.db.base import utc_now
from eidolon_data.events.facade import build_event
from eidolon_data.schema.models import (
    CompanionRow,
    DeviceRow,
    EventRow,
    MemoryRealmRow,
    OwnerRow,
    PersonaGenomeRow,
)

WEB_BODY_KIND = "web"
COMPANION_TYPE_MASTER = "master"
COMPANION_TYPE_SLAVE = "slave"


def _web_body_device_id(companion_id: str) -> str:
    return f"web-{companion_id}"


def _companion_type_from_master(is_master: bool) -> str:
    return COMPANION_TYPE_MASTER if is_master else COMPANION_TYPE_SLAVE


def _companion_type(row: CompanionRow) -> str:
    value = str(getattr(row, "companion_type", "") or "").strip()
    if value in {COMPANION_TYPE_MASTER, COMPANION_TYPE_SLAVE}:
        return value
    return _companion_type_from_master(bool(getattr(row, "is_master", False)))


def _web_body_payload(
    *,
    owner_id: str,
    companion_id: str,
    display_name: str,
    companion_type: str,
) -> dict:
    now = utc_now()
    return {
        "device_id": _web_body_device_id(companion_id),
        "owner_id": owner_id,
        "name": f"{display_name} · 本机",
        "kind": WEB_BODY_KIND,
        "status": "active",
        "approved_at": now,
        "approved_by": "system:onboarding",
        "bound_companion_id": companion_id,
        "interaction_mode": "full_duplex",
        "auth_type": "admin_trust",
        "secret_ref": None,
        "capabilities_json": {
            "audio": True,
            "display": True,
            "text": True,
            "local_web": True,
        },
        "network_json": {},
        "access_policy_json": {
            "conversation": True,
            "voice_input": True,
            "voice_output": True,
            "memory_recall": True,
            "body_commands": False,
        },
        "metadata_json": {
            "auto_provisioned": True,
            "role": "local_web",
            "provisioned_by": "owner_onboarding",
            "companion_type": companion_type,
        },
        "last_seen_at": None,
        "revoked_at": None,
    }


def _build_web_body_row(
    *,
    owner_id: str,
    companion_id: str,
    display_name: str,
    companion_type: str,
) -> DeviceRow:
    """A host-local web body for a companion. Auth is via admin trust (no device
    secret); hub mints its LiveKit token after validating the owner/companion
    binding. kind=web renders as a 虚拟身体 in the cockpit constellation."""
    return DeviceRow(
        **_web_body_payload(
            owner_id=owner_id,
            companion_id=companion_id,
            display_name=display_name,
            companion_type=companion_type,
        )
    )


def _refresh_web_body_row(row: DeviceRow, *, display_name: str, companion_type: str) -> None:
    """Backfill older web body rows into the current standard contract."""
    row.name = row.name or f"{display_name} · 本机"
    row.status = "active"
    row.approved_at = row.approved_at or utc_now()
    row.approved_by = "system:onboarding"
    row.interaction_mode = "full_duplex"
    row.auth_type = "admin_trust"
    row.capabilities_json = {
        **(row.capabilities_json or {}),
        "audio": True,
        "display": True,
        "text": True,
        "local_web": True,
    }
    row.network_json = row.network_json or {}
    row.access_policy_json = {
        **(row.access_policy_json or {}),
        "conversation": True,
        "voice_input": True,
        "voice_output": True,
        "memory_recall": True,
        "body_commands": False,
    }
    row.metadata_json = {
        "auto_provisioned": True,
        "role": "local_web",
        "provisioned_by": "owner_onboarding",
        **(row.metadata_json or {}),
        "companion_type": companion_type,
    }

OWNER_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,47}$")
GENERATED_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,63}$")


class OwnerWorkspaceError(ValueError):
    """Raised when a workspace command violates a domain rule."""


@dataclass(frozen=True)
class OwnerCreateResult:
    owner: OwnerRow


@dataclass(frozen=True)
class CompanionWorkspaceResult:
    companion: CompanionRow
    persona_genome: PersonaGenomeRow
    memory_realm: MemoryRealmRow


class OwnerService:
    def __init__(self, session_factory: async_sessionmaker):
        self._session_factory = session_factory

    async def create_owner(
        self,
        *,
        owner_id: str,
        display_name: str = "",
        kind: str = "person",
        profile_json: dict | None = None,
        settings_json: dict | None = None,
        actor_type: str = "admin",
        actor_id: str | None = None,
    ) -> OwnerCreateResult:
        owner_id = _validate_owner_id(owner_id)
        if kind not in {"person", "family", "team"}:
            raise OwnerWorkspaceError("owner kind must be person, family, or team")

        async with self._session_factory() as session, session.begin():
            existing = await session.get(OwnerRow, owner_id)
            if existing is not None:
                raise OwnerWorkspaceError("owner already exists")

            owner = OwnerRow(
                owner_id=owner_id,
                display_name=display_name or owner_id,
                kind=kind,
                status="active",
                profile_json=profile_json or {},
                settings_json=settings_json or {},
            )
            session.add(owner)
            session.add(
                _event(
                    owner_id=owner_id,
                    subject_type="owner",
                    subject_id=owner_id,
                    event_type="owner.created",
                    actor_type=actor_type,
                    actor_id=actor_id,
                    payload_json={"kind": kind, "display_name": owner.display_name},
                )
            )

        return OwnerCreateResult(owner=owner)


class CompanionWorkspaceService:
    def __init__(self, session_factory: async_sessionmaker):
        self._session_factory = session_factory

    async def ensure_web_body(
        self,
        *,
        owner_id: str,
        companion_id: str,
    ) -> DeviceRow:
        """Idempotently ensure a host-local web body exists for a companion.
        Powers the 'add local web body' one-click and the master default."""
        async with self._session_factory() as session, session.begin():
            companion = await session.get(CompanionRow, companion_id)
            if companion is None or companion.owner_id != owner_id:
                raise OwnerWorkspaceError("companion not found for owner")
            if companion.status != "active":
                raise OwnerWorkspaceError("companion is not active")
            existing = (
                await session.scalars(
                    select(DeviceRow)
                    .where(DeviceRow.bound_companion_id == companion_id)
                    .where(DeviceRow.kind == WEB_BODY_KIND)
                    .where(DeviceRow.revoked_at.is_(None))
                )
            ).first()
            if existing is not None:
                _refresh_web_body_row(
                    existing,
                    display_name=companion.display_name or companion_id,
                    companion_type=_companion_type(companion),
                )
                return existing
            row = _build_web_body_row(
                owner_id=owner_id,
                companion_id=companion_id,
                display_name=companion.display_name or companion_id,
                companion_type=_companion_type(companion),
            )
            session.add(row)
            session.add(
                _event(
                    owner_id=owner_id,
                    companion_id=companion_id,
                    subject_type="device",
                    subject_id=row.device_id,
                    event_type="device.web_body.provisioned",
                    actor_type="admin",
                    actor_id=None,
                    payload_json={"companion_id": companion_id, "kind": WEB_BODY_KIND},
                )
            )
        return row

    async def ensure_memory_realm(
        self,
        *,
        owner_id: str,
        companion_id: str,
        realm_id: str | None = None,
        memory_engine: str = "mempalace",
        memory_engine_config_json: dict | None = None,
        memory_policy_json: dict | None = None,
    ) -> MemoryRealmRow:
        """Idempotently ensure a companion has an active memory realm.

        Parallels ``ensure_web_body`` for the memory side. Returns the existing
        active realm if one is present; otherwise creates ``r_<owner>_default``
        (or the supplied ``realm_id``) and points ``default_memory_realm_id`` at
        it. Reused by master promotion and by the bootstrap path so a companion
        is never left conversation-unready with a body but no memory.
        """
        async with self._session_factory() as session, session.begin():
            companion = await session.get(CompanionRow, companion_id)
            if companion is None or companion.owner_id != owner_id:
                raise OwnerWorkspaceError("companion not found for owner")
            if companion.status != "active":
                raise OwnerWorkspaceError("companion is not active")
            existing = (
                await session.scalars(
                    select(MemoryRealmRow)
                    .where(MemoryRealmRow.companion_id == companion_id)
                    .where(MemoryRealmRow.status == "active")
                )
            ).first()
            if existing is not None:
                if companion.default_memory_realm_id != existing.realm_id:
                    companion.default_memory_realm_id = existing.realm_id
                return existing
            realm_id = realm_id or f"r_{owner_id}_default"
            _validate_generated_id("realm_id", realm_id)
            if await session.get(MemoryRealmRow, realm_id) is not None:
                raise OwnerWorkspaceError(f"realm_id {realm_id!r} already exists")
            realm = MemoryRealmRow(
                realm_id=realm_id,
                owner_id=owner_id,
                companion_id=companion_id,
                engine=memory_engine or "mempalace",
                engine_config_json=memory_engine_config_json or {},
                policy_json=memory_policy_json or {"scope": "owner", "recall": "companion_default"},
                status="active",
            )
            session.add(realm)
            companion.default_memory_realm_id = realm_id
            session.add(
                _event(
                    owner_id=owner_id,
                    companion_id=companion_id,
                    subject_type="memory_realm",
                    subject_id=realm_id,
                    event_type="memory_realm.created",
                    actor_type="admin",
                    actor_id=None,
                    payload_json={"engine": realm.engine},
                )
            )
        return realm

    async def promote_to_master(
        self,
        *,
        owner_id: str,
        companion_id: str,
        actor_type: str = "admin",
        actor_id: str | None = None,
    ) -> CompanionRow:
        """Make ``companion_id`` the owner's master and ensure it is
        conversation-ready (has a current genome, a memory realm, and a
        host-local web body). Any other master for the owner is demoted so the
        one-master-per-owner invariant holds. Idempotent."""
        async with self._session_factory() as session, session.begin():
            companion = await session.get(CompanionRow, companion_id)
            if companion is None or companion.owner_id != owner_id:
                raise OwnerWorkspaceError("companion not found for owner")
            if companion.status != "active":
                raise OwnerWorkspaceError("companion is not active")
            others = await session.scalars(
                select(CompanionRow)
                .where(CompanionRow.owner_id == owner_id)
                .where(CompanionRow.is_master.is_(True))
                .where(CompanionRow.companion_id != companion_id)
            )
            for other in others:
                other.is_master = False
                other.companion_type = COMPANION_TYPE_SLAVE
            companion.is_master = True
            companion.companion_type = COMPANION_TYPE_MASTER
            if not companion.current_genome_id:
                genome = (
                    await session.scalars(
                        select(PersonaGenomeRow)
                        .where(PersonaGenomeRow.companion_id == companion_id)
                        .order_by(PersonaGenomeRow.version.desc())
                    )
                ).first()
                if genome is not None:
                    companion.current_genome_id = genome.genome_id

        # Realm + web body each run in their own idempotent transaction.
        await self.ensure_memory_realm(owner_id=owner_id, companion_id=companion_id)
        await self.ensure_web_body(owner_id=owner_id, companion_id=companion_id)

        async with self._session_factory() as session:
            return await session.get(CompanionRow, companion_id)

    async def provision_workspace(
        self,
        *,
        owner_id: str,
        companion_id: str | None = None,
        companion_display_name: str = "",
        companion_kind: str = "companion",
        companion_profile_json: dict | None = None,
        companion_runtime_config_json: dict | None = None,
        companion_metadata_json: dict | None = None,
        genome_id: str | None = None,
        genome_source_json: dict | None = None,
        genome_json: dict | None = None,
        realm_id: str | None = None,
        memory_engine: str = "mempalace",
        memory_engine_config_json: dict | None = None,
        memory_policy_json: dict | None = None,
        actor_type: str = "admin",
        actor_id: str | None = None,
        is_master: bool = False,
    ) -> CompanionWorkspaceResult:
        owner_id = _validate_owner_id(owner_id)
        companion_id = companion_id or f"c_{owner_id}_default"
        genome_id = genome_id or f"g_{owner_id}_default"
        realm_id = realm_id or f"r_{owner_id}_default"
        _validate_generated_id("companion_id", companion_id)
        _validate_generated_id("genome_id", genome_id)
        _validate_generated_id("realm_id", realm_id)

        async with self._session_factory() as session, session.begin():
            owner = await session.get(OwnerRow, owner_id)
            if owner is None:
                raise OwnerWorkspaceError("owner not found")
            if owner.status != "active":
                raise OwnerWorkspaceError("owner is not active")

            existing_companion = await session.get(CompanionRow, companion_id)
            existing_genome = await session.get(PersonaGenomeRow, genome_id)
            existing_realm = await session.get(MemoryRealmRow, realm_id)
            if existing_companion or existing_genome or existing_realm:
                raise OwnerWorkspaceError("workspace already initialized")

            companion_name = companion_display_name or f"{owner.display_name or owner_id} Companion"
            companion = CompanionRow(
                companion_id=companion_id,
                owner_id=owner_id,
                display_name=companion_name,
                kind=companion_kind,
                status="active",
                is_master=is_master,
                companion_type=_companion_type_from_master(is_master),
                profile_json=companion_profile_json or {},
                runtime_config_json=companion_runtime_config_json or {},
                metadata_json=companion_metadata_json or {},
            )
            session.add(companion)
            await session.flush()

            normalized_genome = _normalize_genome(
                genome_json,
                companion_name,
                source_type=str((genome_source_json or {}).get("source_type") or "admin_workspace_initialize"),
                base_genome_id=None,
            )
            normalized_genome_json = persona_genome_to_json(normalized_genome)
            provenance = dict(normalized_genome_json.get("provenance") or {})
            provenance.update({"owner_id": owner_id, "companion_id": companion_id})
            normalized_genome_json["provenance"] = provenance
            genome = PersonaGenomeRow(
                genome_id=genome_id,
                companion_id=companion_id,
                version=1,
                status="committed",
                base_genome_id=None,
                schema_version=PERSONA_GENOME_SCHEMA,
                genome_hash=persona_genome_hash(normalized_genome_json),
                realizer_version=PERSONA_REALIZER,
                applied_event_id=None,
                source_json=genome_source_json
                or {"source_type": "admin_workspace_initialize", "owner_id": owner_id},
                genome_json=normalized_genome_json,
                change_summary="Initial persona genome",
            )
            session.add(genome)

            realm = MemoryRealmRow(
                realm_id=realm_id,
                owner_id=owner_id,
                companion_id=companion_id,
                engine=memory_engine or "mempalace",
                engine_config_json=memory_engine_config_json or {},
                policy_json=memory_policy_json or {"scope": "owner", "recall": "companion_default"},
                status="active",
            )
            session.add(realm)

            companion.current_genome_id = genome_id
            companion.default_memory_realm_id = realm_id

            # Master companion defaults to a host-local web body.
            if is_master:
                web_body = _build_web_body_row(
                    owner_id=owner_id,
                    companion_id=companion_id,
                    display_name=companion_name,
                    companion_type=_companion_type_from_master(is_master),
                )
                session.add(web_body)
                session.add(
                    _event(
                        owner_id=owner_id,
                        companion_id=companion_id,
                        subject_type="device",
                        subject_id=web_body.device_id,
                        event_type="device.web_body.provisioned",
                        actor_type=actor_type,
                        actor_id=actor_id,
                        payload_json={"companion_id": companion_id, "kind": WEB_BODY_KIND},
                    )
                )

            event_specs = [
                (
                    "companion",
                    companion_id,
                    "companion.created",
                    {
                        "display_name": companion_name,
                        "companion_type": _companion_type_from_master(is_master),
                    },
                ),
                (
                    "persona_genome",
                    genome_id,
                    "persona.genome.committed",
                    {
                        "version": 1,
                        "genome_id": genome_id,
                        "genome_hash": genome.genome_hash,
                        "schema_version": genome.schema_version,
                        "realizer_version": genome.realizer_version,
                    },
                ),
                ("memory_realm", realm_id, "memory_realm.created", {"engine": realm.engine}),
                (
                    "companion",
                    companion_id,
                    "companion.workspace.initialized",
                    {"genome_id": genome_id, "realm_id": realm_id},
                ),
            ]
            for subject_type, subject_id, event_type, payload_json in event_specs:
                event = _event(
                    owner_id=owner_id,
                    companion_id=companion_id,
                    subject_type=subject_type,
                    subject_id=subject_id,
                    event_type=event_type,
                    actor_type=actor_type,
                    actor_id=actor_id,
                    payload_json=payload_json,
                )
                if event_type == "persona.genome.committed":
                    genome.applied_event_id = event.event_id
                session.add(event)

        return CompanionWorkspaceResult(
            companion=companion,
            persona_genome=genome,
            memory_realm=realm,
        )


def _validate_owner_id(owner_id: str) -> str:
    owner_id = owner_id.strip()
    if not OWNER_ID_RE.match(owner_id):
        raise OwnerWorkspaceError(
            "owner_id must be 1-48 chars and contain only letters, numbers, _, ., or -"
        )
    return owner_id


def _validate_generated_id(label: str, value: str) -> None:
    if not GENERATED_ID_RE.match(value):
        raise OwnerWorkspaceError(
            f"{label} must be 1-64 chars and contain only letters, numbers, _, ., or -"
        )


def _default_genome(display_name: str) -> dict:
    return persona_genome_to_json(
        build_default_persona_genome(name=display_name, origin="template")
    )


def _normalize_genome(
    genome_json: dict | None,
    display_name: str,
    *,
    source_type: str = "template",
    base_genome_id: str | None = None,
):
    if genome_json is None:
        return build_default_persona_genome(
            name=display_name,
            archetype="companion",
            origin=source_type,
            base_genome_id=base_genome_id,
        )
    return normalize_persona_genome(genome_json)


def _event(
    *,
    owner_id: str,
    subject_type: str,
    subject_id: str,
    event_type: str,
    actor_type: str,
    actor_id: str | None,
    payload_json: dict,
    companion_id: str | None = None,
) -> EventRow:
    # Route through the contract-carrying facade (fills tier/source/severity/
    # outcome from the catalog); returned unpersisted for same-transaction add.
    return build_event(
        event_type=event_type,
        owner_id=owner_id,
        companion_id=companion_id,
        subject_type=subject_type,
        subject_id=subject_id,
        actor_type=actor_type,
        actor_id=actor_id,
        payload_json=payload_json,
    )
