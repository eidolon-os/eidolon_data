"""Application services for Owner and Companion workspace lifecycle.

These commands own low-frequency sovereign configuration only. Device
admission, runtime sessions, memory payloads, and command delivery are outside
the System Data authority boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID, uuid4

from eidolon_sdk.biz.persona import (
    PERSONA_GENOME_SCHEMA,
    PERSONA_REALIZER,
    build_default_persona_genome,
    normalize_persona_genome,
    persona_genome_hash,
    persona_genome_to_json,
)
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from eidolon_data.audit import governance_fact
from eidolon_data.db.base import utc_now
from eidolon_data.schema import (
    CompanionRow,
    GuardBindingRow,
    MemoryRealmRow,
    OwnerRow,
    PersonaGenomeRow,
)

OWNER_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,47}$")
GENERATED_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
REQUEST_FINGERPRINT_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
COMPANION_ROLES = frozenset({"primary", "standard", "guard"})
ONBOARDING_METADATA_KEY = "_eidolon_onboarding"


class OwnerWorkspaceError(ValueError):
    """Raised when a workspace command violates a domain invariant."""


@dataclass(frozen=True)
class OwnerCreateResult:
    owner: OwnerRow


@dataclass(frozen=True)
class CompanionWorkspaceResult:
    companion: CompanionRow
    persona_genome: PersonaGenomeRow
    memory_realm: MemoryRealmRow


@dataclass(frozen=True)
class OwnerWorkspaceInitializationResult:
    operation_id: str
    request_fingerprint: str
    owner: OwnerRow
    workspace: CompanionWorkspaceResult


class OwnerService:
    """Transactional commands for the Owner aggregate."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def create_owner(
        self,
        *,
        owner_id: str,
        display_name: str = "",
        kind: str = "person",
        profile_json: dict | None = None,
        settings_json: dict | None = None,
    ) -> OwnerCreateResult:
        owner_id = _validate_owner_id(owner_id)
        if kind not in {"person", "family", "team"}:
            raise OwnerWorkspaceError("owner kind must be person, family, or team")

        async with self._session_factory() as session, session.begin():
            if await session.get(OwnerRow, owner_id) is not None:
                raise OwnerWorkspaceError("owner already exists")
            owner = OwnerRow(
                owner_id=owner_id,
                display_name=display_name.strip() or owner_id,
                kind=kind,
                status="active",
                profile_json=dict(profile_json or {}),
                settings_json=dict(settings_json or {}),
            )
            session.add(owner)
            session.add(
                governance_fact(
                    owner_id=owner_id,
                    subject_type="owner",
                    subject_id=owner_id,
                    action="owner.created",
                    payload={"kind": kind, "display_name": owner.display_name},
                )
            )
        return OwnerCreateResult(owner=owner)

    async def update_owner(
        self,
        *,
        owner_id: str,
        display_name: str | None = None,
        profile_json: dict | None = None,
        settings_json: dict | None = None,
    ) -> OwnerRow:
        async with self._session_factory() as session, session.begin():
            owner = await session.get(OwnerRow, owner_id)
            if owner is None:
                raise KeyError(f"owner not found: {owner_id}")
            if owner.status != "active":
                raise OwnerWorkspaceError("only an active owner can be updated")
            changed: list[str] = []
            if display_name is not None:
                value = display_name.strip()
                if not value:
                    raise OwnerWorkspaceError("display_name cannot be blank")
                owner.display_name = value
                changed.append("display_name")
            if profile_json is not None:
                owner.profile_json = dict(profile_json)
                changed.append("profile")
            if settings_json is not None:
                owner.settings_json = dict(settings_json)
                changed.append("settings")
            if not changed:
                return owner
            owner.updated_at = utc_now()
            session.add(
                governance_fact(
                    owner_id=owner_id,
                    subject_type="owner",
                    subject_id=owner_id,
                    action="owner.updated",
                    payload={"changed_fields": changed},
                )
            )
        return owner

    async def archive_owner(self, owner_id: str) -> OwnerRow:
        async with self._session_factory() as session, session.begin():
            owner = await session.get(OwnerRow, owner_id)
            if owner is None:
                raise KeyError(f"owner not found: {owner_id}")
            if owner.status == "archived":
                return owner
            if owner.status != "active":
                raise OwnerWorkspaceError(f"cannot archive owner in state {owner.status}")
            owner.status = "archived"
            owner.updated_at = utc_now()
            companions = await session.scalars(
                select(CompanionRow).where(
                    CompanionRow.owner_id == owner_id,
                    CompanionRow.status == "active",
                )
            )
            for companion in companions:
                companion.status = "inactive"
                companion.updated_at = owner.updated_at
            realms = await session.execute(
                update(MemoryRealmRow)
                .where(
                    MemoryRealmRow.owner_id == owner_id,
                    MemoryRealmRow.status == "active",
                )
                .values(status="inactive", updated_at=owner.updated_at)
            )
            bindings = await session.execute(
                update(GuardBindingRow)
                .where(
                    GuardBindingRow.owner_id == owner_id,
                    GuardBindingRow.state == "active",
                )
                .values(
                    state="disabled",
                    disabled_at=owner.updated_at,
                    updated_at=owner.updated_at,
                )
            )
            session.add(
                governance_fact(
                    owner_id=owner_id,
                    subject_type="owner",
                    subject_id=owner_id,
                    action="owner.archived",
                    payload={
                        "memory_realms_inactivated": int(realms.rowcount or 0),
                        "guard_bindings_disabled": int(bindings.rowcount or 0),
                    },
                )
            )
        return owner


class CompanionWorkspaceService:
    """Atomic Companion identity, Persona, and Memory catalog commands."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def initialize_owner_workspace(
        self,
        *,
        operation_id: str,
        request_fingerprint: str,
        owner_display_name: str,
        companion_display_name: str,
    ) -> OwnerWorkspaceInitializationResult:
        """Atomically initialize the first Owner workspace for one operation.

        The operation UUID determines every aggregate identifier. The immutable
        request fingerprint is stored as Companion provenance, so retries can
        reconstruct the same result without a second workflow database.
        """

        canonical_operation_id = _validate_operation_id(operation_id)
        if not REQUEST_FINGERPRINT_RE.fullmatch(request_fingerprint):
            raise OwnerWorkspaceError("request_fingerprint must be a sha256 digest")
        owner_name = owner_display_name.strip()
        companion_name = companion_display_name.strip()
        if not owner_name:
            raise OwnerWorkspaceError("owner_display_name cannot be blank")
        if not companion_name:
            raise OwnerWorkspaceError("companion_display_name cannot be blank")
        ids = _onboarding_ids(canonical_operation_id)

        async with self._session_factory() as session, session.begin():
            existing_owner = await session.get(OwnerRow, ids["owner_id"])
            if existing_owner is not None:
                return await _load_onboarding_result(
                    session,
                    operation_id=canonical_operation_id,
                    request_fingerprint=request_fingerprint,
                )

            owner = OwnerRow(
                owner_id=ids["owner_id"],
                display_name=owner_name,
                kind="person",
                status="active",
                profile_json={},
                settings_json={},
            )
            session.add(owner)
            session.add(
                governance_fact(
                    owner_id=owner.owner_id,
                    subject_type="owner",
                    subject_id=owner.owner_id,
                    action="owner.created",
                    payload={"kind": owner.kind, "display_name": owner.display_name},
                    trace_id=canonical_operation_id,
                )
            )
            await session.flush()
            workspace = await _provision_workspace_in_session(
                session,
                owner_id=owner.owner_id,
                companion_id=ids["companion_id"],
                companion_display_name=companion_name,
                role="primary",
                companion_profile_json={},
                companion_runtime_config_json={},
                companion_metadata_json={
                    "source": "owner_onboarding",
                    ONBOARDING_METADATA_KEY: {
                        "operation_id": canonical_operation_id,
                        "request_fingerprint": request_fingerprint,
                    },
                },
                genome_id=ids["genome_id"],
                genome_source_json={
                    "source_type": "owner_onboarding",
                    "owner_id": owner.owner_id,
                    "operation_id": canonical_operation_id,
                },
                genome_json=None,
                realm_id=ids["realm_id"],
                memory_engine="mempalace",
                memory_engine_config_json={},
                memory_policy_json=None,
                trace_id=canonical_operation_id,
            )
            return OwnerWorkspaceInitializationResult(
                operation_id=canonical_operation_id,
                request_fingerprint=request_fingerprint,
                owner=owner,
                workspace=workspace,
            )

    async def get_owner_workspace_initialization(
        self,
        operation_id: str,
    ) -> OwnerWorkspaceInitializationResult | None:
        """Reconstruct one durable initialization result from its aggregates."""

        canonical_operation_id = _validate_operation_id(operation_id)
        ids = _onboarding_ids(canonical_operation_id)
        async with self._session_factory() as session:
            if await session.get(OwnerRow, ids["owner_id"]) is None:
                return None
            return await _load_onboarding_result(
                session,
                operation_id=canonical_operation_id,
                request_fingerprint=None,
            )

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
        """Idempotently ensure the catalog pointer; never operate on memory data."""

        async with self._session_factory() as session, session.begin():
            companion = await _owned_active_companion(session, owner_id, companion_id)
            realm, created = await _ensure_memory_realm_in_session(
                session,
                companion=companion,
                realm_id=realm_id,
                memory_engine=memory_engine,
                memory_engine_config_json=memory_engine_config_json,
                memory_policy_json=memory_policy_json,
            )
            if created:
                session.add(
                    governance_fact(
                        owner_id=owner_id,
                        subject_type="memory_realm",
                        subject_id=realm.realm_id,
                        action="memory_realm.cataloged",
                        payload={"companion_id": companion_id, "engine": realm.engine},
                    )
                )
        return realm

    async def promote_to_primary(
        self,
        *,
        owner_id: str,
        companion_id: str,
    ) -> CompanionRow:
        async with self._session_factory() as session, session.begin():
            companion = await _owned_active_companion(session, owner_id, companion_id)
            if companion.role == "guard":
                raise OwnerWorkspaceError("a guard companion cannot become primary")
            previous = await session.scalar(
                select(CompanionRow).where(
                    CompanionRow.owner_id == owner_id,
                    CompanionRow.role == "primary",
                    CompanionRow.status == "active",
                    CompanionRow.companion_id != companion_id,
                )
            )
            if previous is not None:
                previous.role = "standard"
                previous.updated_at = utc_now()
                # The partial unique index is evaluated per UPDATE in SQLite.
                # Release it before assigning the new primary role.
                await session.flush()
            if companion.role != "primary":
                companion.role = "primary"
                companion.updated_at = utc_now()
                session.add(
                    governance_fact(
                        owner_id=owner_id,
                        subject_type="companion",
                        subject_id=companion_id,
                        action="companion.promoted_to_primary",
                        payload={
                            "previous_primary_id": (
                                previous.companion_id if previous is not None else None
                            )
                        },
                    )
                )
            realm, created = await _ensure_memory_realm_in_session(
                session,
                companion=companion,
            )
            if created:
                session.add(
                    governance_fact(
                        owner_id=owner_id,
                        subject_type="memory_realm",
                        subject_id=realm.realm_id,
                        action="memory_realm.cataloged",
                        payload={"companion_id": companion_id, "engine": realm.engine},
                    )
                )
        return companion

    async def provision_workspace(
        self,
        *,
        owner_id: str,
        companion_id: str | None = None,
        companion_display_name: str = "",
        role: str = "standard",
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
    ) -> CompanionWorkspaceResult:
        owner_id = _validate_owner_id(owner_id)
        if role not in COMPANION_ROLES:
            raise OwnerWorkspaceError("role must be primary, standard, or guard")
        resolved_companion_id = companion_id or f"c_{owner_id}"
        resolved_genome_id = genome_id or f"g_{uuid4().hex}"
        resolved_realm_id = realm_id or f"r_{uuid4().hex}"
        for label, value in (
            ("companion_id", resolved_companion_id),
            ("genome_id", resolved_genome_id),
            ("realm_id", resolved_realm_id),
        ):
            _validate_generated_id(label, value)

        async with self._session_factory() as session, session.begin():
            return await _provision_workspace_in_session(
                session,
                owner_id=owner_id,
                companion_id=resolved_companion_id,
                companion_display_name=companion_display_name,
                role=role,
                companion_profile_json=companion_profile_json,
                companion_runtime_config_json=companion_runtime_config_json,
                companion_metadata_json=companion_metadata_json,
                genome_id=resolved_genome_id,
                genome_source_json=genome_source_json,
                genome_json=genome_json,
                realm_id=resolved_realm_id,
                memory_engine=memory_engine,
                memory_engine_config_json=memory_engine_config_json,
                memory_policy_json=memory_policy_json,
            )


async def _provision_workspace_in_session(
    session,
    *,
    owner_id: str,
    companion_id: str,
    companion_display_name: str,
    role: str,
    companion_profile_json: dict | None,
    companion_runtime_config_json: dict | None,
    companion_metadata_json: dict | None,
    genome_id: str,
    genome_source_json: dict | None,
    genome_json: dict | None,
    realm_id: str,
    memory_engine: str,
    memory_engine_config_json: dict | None,
    memory_policy_json: dict | None,
    trace_id: str | None = None,
) -> CompanionWorkspaceResult:
    owner = await session.get(OwnerRow, owner_id)
    if owner is None:
        raise OwnerWorkspaceError("owner not found")
    if owner.status != "active":
        raise OwnerWorkspaceError("owner is not active")
    if any(
        (
            await session.get(CompanionRow, companion_id),
            await session.get(PersonaGenomeRow, genome_id),
            await session.get(MemoryRealmRow, realm_id),
        )
    ):
        raise OwnerWorkspaceError("workspace identifier already exists")
    if role == "primary" and await session.scalar(
        select(CompanionRow.companion_id).where(
            CompanionRow.owner_id == owner_id,
            CompanionRow.role == "primary",
            CompanionRow.status == "active",
        )
    ):
        raise OwnerWorkspaceError("owner already has an active primary companion")

    companion_name = companion_display_name.strip() or f"{owner.display_name or owner_id} Companion"
    companion = CompanionRow(
        companion_id=companion_id,
        owner_id=owner_id,
        display_name=companion_name,
        role=role,
        status="active",
        profile_json=dict(companion_profile_json or {}),
        runtime_config_json=dict(companion_runtime_config_json or {}),
        metadata_json=dict(companion_metadata_json or {}),
    )
    session.add(companion)
    await session.flush()

    normalized = _normalize_genome(
        genome_json,
        companion_name,
        source_type=str((genome_source_json or {}).get("source_type") or "workspace_initialize"),
    )
    normalized_json = persona_genome_to_json(normalized)
    normalized_json["provenance"] = {
        **dict(normalized_json.get("provenance") or {}),
        "owner_id": owner_id,
        "companion_id": companion_id,
    }
    genome = PersonaGenomeRow(
        genome_id=genome_id,
        companion_id=companion_id,
        version=1,
        status="committed",
        base_genome_id=None,
        schema_version=PERSONA_GENOME_SCHEMA,
        genome_hash=persona_genome_hash(normalized_json),
        realizer_version=PERSONA_REALIZER,
        source_json=dict(
            genome_source_json or {"source_type": "workspace_initialize", "owner_id": owner_id}
        ),
        genome_json=normalized_json,
        change_summary="Initial persona genome",
    )
    session.add(genome)
    await session.flush()
    companion.current_genome_id = genome.genome_id
    # The Owner's memory, created on the first Companion and shared by every
    # one after it. One code path for "point this Companion at its Owner's
    # memory" — a second one here is how you end up with a realm per Companion
    # again, and the unique index would only tell you afterwards.
    realm, _created = await _ensure_memory_realm_in_session(
        session,
        companion=companion,
        realm_id=realm_id,
        memory_engine=memory_engine,
        memory_engine_config_json=memory_engine_config_json,
        memory_policy_json=memory_policy_json,
    )
    fact = governance_fact(
        owner_id=owner_id,
        subject_type="companion",
        subject_id=companion_id,
        action="companion.workspace.initialized",
        payload={
            "role": role,
            "genome_id": genome.genome_id,
            "realm_id": realm.realm_id,
        },
        trace_id=trace_id,
    )
    genome.applied_event_id = fact.event_id
    session.add(fact)
    return CompanionWorkspaceResult(
        companion=companion,
        persona_genome=genome,
        memory_realm=realm,
    )


async def _load_onboarding_result(
    session,
    *,
    operation_id: str,
    request_fingerprint: str | None,
) -> OwnerWorkspaceInitializationResult:
    ids = _onboarding_ids(operation_id)
    owner = await session.get(OwnerRow, ids["owner_id"])
    companion = await session.get(CompanionRow, ids["companion_id"])
    genome = await session.get(PersonaGenomeRow, ids["genome_id"])
    realm = await session.get(MemoryRealmRow, ids["realm_id"])
    if owner is None or companion is None or genome is None or realm is None:
        raise OwnerWorkspaceError("workspace initialization resources are incomplete")
    metadata = (companion.metadata_json or {}).get(ONBOARDING_METADATA_KEY)
    if not isinstance(metadata, dict):
        raise OwnerWorkspaceError("workspace identifiers belong to another operation")
    stored_operation_id = metadata.get("operation_id")
    stored_fingerprint = metadata.get("request_fingerprint")
    if stored_operation_id != operation_id or not isinstance(stored_fingerprint, str):
        raise OwnerWorkspaceError("workspace identifiers belong to another operation")
    if request_fingerprint is not None and stored_fingerprint != request_fingerprint:
        raise OwnerWorkspaceError("operation_id is already in use for another request")
    if (
        companion.owner_id != owner.owner_id
        or companion.role != "primary"
        or companion.current_genome_id != genome.genome_id
        or companion.default_memory_realm_id != realm.realm_id
        or genome.companion_id != companion.companion_id
        or realm.owner_id != owner.owner_id
    ):
        raise OwnerWorkspaceError("workspace initialization resources are inconsistent")
    return OwnerWorkspaceInitializationResult(
        operation_id=operation_id,
        request_fingerprint=stored_fingerprint,
        owner=owner,
        workspace=CompanionWorkspaceResult(
            companion=companion,
            persona_genome=genome,
            memory_realm=realm,
        ),
    )


async def _owned_active_companion(session, owner_id: str, companion_id: str) -> CompanionRow:
    companion = await session.get(CompanionRow, companion_id)
    if companion is None or companion.owner_id != owner_id:
        raise OwnerWorkspaceError("companion not found for owner")
    if companion.status != "active":
        raise OwnerWorkspaceError("companion is not active")
    return companion


async def _ensure_memory_realm_in_session(
    session,
    *,
    companion: CompanionRow,
    realm_id: str | None = None,
    memory_engine: str = "mempalace",
    memory_engine_config_json: dict | None = None,
    memory_policy_json: dict | None = None,
) -> tuple[MemoryRealmRow, bool]:
    """Point this Companion at its Owner's memory, creating it only once.

    The Owner has one realm and every Companion shares it, so this creates a
    realm for the Owner's first Companion and then only repoints. There is no
    per-Companion realm to create — an Owner's second Companion adds a name and
    a persona, not a second memory.
    """
    existing = await session.scalar(
        select(MemoryRealmRow)
        .where(
            MemoryRealmRow.owner_id == companion.owner_id,
            MemoryRealmRow.status == "active",
        )
        .order_by(MemoryRealmRow.created_at)
    )
    if existing is not None:
        companion.default_memory_realm_id = existing.realm_id
        companion.updated_at = utc_now()
        return existing, False

    resolved_realm_id = realm_id or f"r_{uuid4().hex}"
    _validate_generated_id("realm_id", resolved_realm_id)
    if await session.get(MemoryRealmRow, resolved_realm_id) is not None:
        raise OwnerWorkspaceError(f"realm_id {resolved_realm_id!r} already exists")
    realm = MemoryRealmRow(
        realm_id=resolved_realm_id,
        owner_id=companion.owner_id,
        engine=memory_engine.strip() or "mempalace",
        engine_config_json=dict(memory_engine_config_json or {}),
        policy_json=dict(memory_policy_json or {"scope": "owner", "recall": "companion_default"}),
        status="active",
    )
    session.add(realm)
    await session.flush()
    companion.default_memory_realm_id = resolved_realm_id
    companion.updated_at = utc_now()
    return realm, True


def _validate_owner_id(owner_id: str) -> str:
    value = owner_id.strip()
    if value != owner_id or not OWNER_ID_RE.fullmatch(value):
        raise OwnerWorkspaceError(
            "owner_id must be 1-48 chars and contain only letters, numbers, _, ., or -"
        )
    return value


def _validate_operation_id(operation_id: str) -> str:
    try:
        canonical = str(UUID(operation_id))
    except (ValueError, AttributeError) as exc:
        raise OwnerWorkspaceError("operation_id must be a UUID") from exc
    if canonical != operation_id:
        raise OwnerWorkspaceError("operation_id must use canonical UUID encoding")
    return canonical


def _onboarding_ids(operation_id: str) -> dict[str, str]:
    operation_hex = UUID(operation_id).hex
    return {
        "owner_id": f"owner_{operation_hex}",
        "companion_id": f"c_{operation_hex}",
        "genome_id": f"g_{operation_hex}_origin",
        "realm_id": f"r_{operation_hex}",
    }


def _validate_generated_id(label: str, value: str) -> None:
    if not GENERATED_ID_RE.fullmatch(value):
        raise OwnerWorkspaceError(
            f"{label} must be 1-64 chars and contain only letters, numbers, _, ., or -"
        )


def _normalize_genome(
    genome_json: dict | None,
    display_name: str,
    *,
    source_type: str,
):
    if genome_json is not None:
        return normalize_persona_genome(genome_json)
    return build_default_persona_genome(
        name=display_name,
        archetype="companion",
        origin=source_type,
    )
