"""Domain services for owner-scoped workspace lifecycle."""

from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

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


def _web_body_device_id(companion_id: str) -> str:
    return f"web-{companion_id}"


def _build_web_body_row(*, owner_id: str, companion_id: str, display_name: str) -> DeviceRow:
    """A host-local web body for a companion. Auth is via admin trust (no device
    secret); hub mints its LiveKit token after validating the owner/companion
    binding. kind=web renders as a 虚拟身体 in the cockpit constellation."""
    now = utc_now()
    return DeviceRow(
        device_id=_web_body_device_id(companion_id),
        owner_id=owner_id,
        name=f"{display_name} · 本机",
        kind=WEB_BODY_KIND,
        status="active",
        approved_at=now,
        approved_by="system",
        bound_companion_id=companion_id,
        interaction_mode="full_duplex",
        capabilities_json={"audio": True, "display": True},
        metadata_json={"auto_provisioned": True, "role": "local_web"},
    )

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

    async def initialize_workspace(
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
        prompt_markdown: str = "",
        evolution_state_json: dict | None = None,
        realm_id: str | None = None,
        memory_engine: str = "mempalace",
        memory_engine_config_json: dict | None = None,
        memory_policy_json: dict | None = None,
        actor_type: str = "admin",
        actor_id: str | None = None,
        is_master: bool = False,
    ) -> CompanionWorkspaceResult:
        """Compatibility wrapper for owner/companion provisioning.

        New call sites should use ``provision_workspace``; this name remains
        for existing admin/agent tests and callers.
        """

        return await self.provision_workspace(
            owner_id=owner_id,
            companion_id=companion_id,
            companion_display_name=companion_display_name,
            companion_kind=companion_kind,
            companion_profile_json=companion_profile_json,
            companion_runtime_config_json=companion_runtime_config_json,
            companion_metadata_json=companion_metadata_json,
            genome_id=genome_id,
            genome_source_json=genome_source_json,
            genome_json=genome_json,
            prompt_markdown=prompt_markdown,
            evolution_state_json=evolution_state_json,
            realm_id=realm_id,
            memory_engine=memory_engine,
            memory_engine_config_json=memory_engine_config_json,
            memory_policy_json=memory_policy_json,
            actor_type=actor_type,
            actor_id=actor_id,
            is_master=is_master,
        )

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
                return existing
            row = _build_web_body_row(
                owner_id=owner_id,
                companion_id=companion_id,
                display_name=companion.display_name or companion_id,
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
        prompt_markdown: str = "",
        evolution_state_json: dict | None = None,
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
        genome_id = genome_id or f"g_{owner_id}_default_v1"
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
                profile_json=companion_profile_json or {},
                runtime_config_json=companion_runtime_config_json or {},
                metadata_json=companion_metadata_json or {},
            )
            session.add(companion)
            await session.flush()

            genome = PersonaGenomeRow(
                genome_id=genome_id,
                companion_id=companion_id,
                version=1,
                status="committed",
                base_genome_id=None,
                source_json=genome_source_json
                or {"source_type": "admin_workspace_initialize", "owner_id": owner_id},
                genome_json=_normalize_genome(genome_json, companion_name),
                prompt_markdown=prompt_markdown or _default_prompt_markdown(companion_name),
                evolution_state_json=evolution_state_json or {"version": 1, "mode": "continuous"},
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
                    owner_id=owner_id, companion_id=companion_id, display_name=companion_name
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
                ("companion", companion_id, "companion.created", {"display_name": companion_name}),
                ("persona_genome", genome_id, "persona_genome.created", {"version": 1}),
                ("memory_realm", realm_id, "memory_realm.created", {"engine": realm.engine}),
                (
                    "companion",
                    companion_id,
                    "companion.workspace.initialized",
                    {"genome_id": genome_id, "realm_id": realm_id},
                ),
            ]
            for subject_type, subject_id, event_type, payload_json in event_specs:
                session.add(
                    _event(
                        owner_id=owner_id,
                        companion_id=companion_id,
                        subject_type=subject_type,
                        subject_id=subject_id,
                        event_type=event_type,
                        actor_type=actor_type,
                        actor_id=actor_id,
                        payload_json=payload_json,
                    )
                )

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
    return {
        "identity": {"name": display_name, "archetype": "companion"},
        "style": {"tone": "warm", "initiative": "balanced"},
        "boundaries": {},
        "evolution": {"enabled": True},
    }


def _normalize_genome(genome_json: dict | None, display_name: str) -> dict:
    genome = dict(genome_json or _default_genome(display_name))
    identity = dict(genome.get("identity") or {})
    if not str(identity.get("name") or "").strip():
        identity["name"] = display_name
    if not str(identity.get("archetype") or "").strip():
        identity["archetype"] = "companion"
    genome["identity"] = identity
    return genome


def _default_prompt_markdown(display_name: str) -> str:
    return (
        f"# {display_name}\n\n"
        "## Identity\n\n"
        f"- Name: {display_name}\n"
        "- Archetype: companion\n\n"
        "## Style\n\n"
        "- Warm, clear, and grounded.\n"
        "- Respond to the user's intent before adding suggestions.\n"
        "- Keep healthy boundaries and avoid pretending to know what was not provided.\n\n"
        "## Evolution\n\n"
        "- This persona may evolve through reviewed or policy-approved genome versions.\n"
    )


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
