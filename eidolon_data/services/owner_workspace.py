"""Domain services for owner-scoped workspace lifecycle."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.ext.asyncio import async_sessionmaker

from eidolon_data.schema.models import (
    CompanionRow,
    EventRow,
    MemoryRealmRow,
    OwnerRow,
    PersonaGenomeRow,
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
        )

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
) -> EventRow:
    return EventRow(
        event_id=f"evt_{uuid4().hex}",
        owner_id=owner_id,
        subject_type=subject_type,
        subject_id=subject_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        payload_json=payload_json,
    )
