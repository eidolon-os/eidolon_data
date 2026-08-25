"""Application services for Owner and Companion workspace lifecycle.

These commands own low-frequency sovereign configuration only. Device
admission, runtime sessions, memory payloads, and command delivery are outside
the System Data authority boundary.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID, uuid4

from eidolon_sdk.biz.contracts.companion import (
    COMPANION_LIFECYCLE_TRANSITIONS,
    DEFAULT_ELIGIBLE_LIFECYCLE_STATES,
    CompanionLifecycleConflictCode,
)
from eidolon_sdk.biz.persona import (
    PERSONA_GENOME_SCHEMA,
    PERSONA_REALIZER,
    PersonaAuthoring,
    PersonaAuthoringDraft,
    build_default_persona_genome,
    build_persona_genome_from_draft,
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
#: What a Companion is, as a product type. Which of these are offered to an
#: Owner is a capability decision made above this layer; this set is what the
#: storage will accept. "Which one is the default" is deliberately not here —
#: it is a pointer on the Owner, not a kind.
COMPANION_KINDS = frozenset({"conversational", "guard", "specialist", "system"})
ONBOARDING_METADATA_KEY = "_eidolon_onboarding"
#: Provenance for a Companion added to an Owner who already has one. Its own key
#: rather than reusing the onboarding one, because these are different events: an
#: Owner is created once, and Companions are added many times. Sharing the key
#: would make "was this the first?" unanswerable.
PROVISION_METADATA_KEY = "_eidolon_companion_provision"


class OwnerWorkspaceError(ValueError):
    """Raised when a workspace command violates a domain invariant."""


class OwnerWorkspaceNotFound(OwnerWorkspaceError):
    """The subject of the command is not this Owner's, or is not there.

    One exception for both, because they must be answered identically: an id
    that says "forbidden" rather than "absent" can be probed for existence.
    """


class OwnerWorkspaceConflict(OwnerWorkspaceError):
    """The caller's view of the aggregate is older than the aggregate.

    Its own type rather than a recognisable message. The status mapping used to
    read the exception's text for substrings like "already in use", which means
    a rephrased sentence silently changes an HTTP status.
    """


class CompanionLifecycleConflict(OwnerWorkspaceError):
    """A lifecycle command the authority refused, and why in a word.

    Its own type carrying a ``code`` rather than a recognisable sentence: these
    refusals travel two process boundaries before a person reads one, and they
    call for different things — a re-read, a question ("which Companion should
    answer instead?"), or nothing at all because the caller is retrying something
    that already happened.
    """

    def __init__(self, message: str, *, code: CompanionLifecycleConflictCode) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class OwnerCreateResult:
    owner: OwnerRow


@dataclass(frozen=True)
class CompanionWorkspaceResult:
    companion: CompanionRow
    persona_genome: PersonaGenomeRow
    memory_realm: MemoryRealmRow


@dataclass(frozen=True)
class CompanionProvisionResult:
    """One added Companion, and whether the Owner's memory had to be created.

    ``memory_realm_created`` is a fact the caller needs rather than a curiosity:
    a realm that already existed needs no runtime signal, and a realm that was
    just catalogued does — a row in a table is not a running process (§II-6.4).
    Publishing it here is what lets the caller send that signal *only* when
    there is something to reconcile, instead of on every create.

    ``replayed`` says this operation had already been carried out. The result is
    identical either way; the flag exists so a caller can tell "I did this" from
    "this was already done" without diffing.
    """

    operation_id: str
    request_fingerprint: str
    companion: CompanionRow
    persona_genome: PersonaGenomeRow
    memory_realm: MemoryRealmRow
    memory_realm_created: bool
    replayed: bool


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
                    CompanionRow.lifecycle_state == "active",
                )
            )
            for companion in companions:
                companion.lifecycle_state = "archived"
                companion.revision += 1
                companion.updated_at = owner.updated_at
            # An archived Owner has no default Companion to route to, and a
            # pointer left behind would outlive what it points at.
            owner.default_companion_id = None
            owner.revision += 1
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
                kind="conversational",
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

    async def begin_retirement(
        self,
        *,
        owner_id: str,
        companion_id: str,
        expected_revision: int | None = None,
        replacement_companion_id: str | None = None,
    ) -> CompanionRow:
        """Start putting a Companion away: no new sessions, no new Body work.

        The first of the two steps, and the reason there are two. Everything that
        has to stop — new sessions, new Body assignments, becoming the default —
        stops here, while the Companion is still answerable; archiving is what
        happens after those have drained. A single command that did both would be
        archiving a Companion that something is still talking to.

        **If this is the Companion the Owner's unaddressed requests go to, the
        same request has to say who takes over.** Not a later step: between the
        two there would be an Owner whose Eidolon cannot answer, and no amount of
        ordering inside a workflow makes that window not exist. When there is
        nobody to hand it to, the command refuses — an Owner left with one
        archived Companion has no Eidolon at all.

        Repeating it is a success. A Companion already retiring is the state the
        caller asked for, and a retry after a lost answer must not produce a
        second governance event for one decision.
        """

        async with self._session_factory() as session, session.begin():
            companion = await _owned_companion(session, owner_id, companion_id)
            await self._retire(
                session,
                owner_id=owner_id,
                companion=companion,
                expected_revision=expected_revision,
                replacement_companion_id=replacement_companion_id,
            )
            return companion

    async def _retire(
        self,
        session,
        *,
        owner_id: str,
        companion: CompanionRow,
        expected_revision: int | None,
        replacement_companion_id: str | None,
    ) -> None:
        """The retiring half, on a session someone else opened.

        Split out so that "put this away" can be one transaction while the two
        steps stay separately callable. See ``put_away_companion``.
        """

        companion_id = companion.companion_id
        if companion.lifecycle_state == "retiring":
            return
        _require_transition(companion, "retiring", expected_revision)
        owner = await session.get(OwnerRow, owner_id)
        if owner is None:
            raise OwnerWorkspaceNotFound("owner not found")
        replacement: CompanionRow | None = None
        if owner.default_companion_id == companion_id:
            replacement = await _eligible_replacement(
                session,
                owner_id=owner_id,
                retiring_id=companion_id,
                replacement_companion_id=replacement_companion_id,
            )
            owner.default_companion_id = replacement.companion_id
            owner.revision += 1
            owner.updated_at = utc_now()
            session.add(
                governance_fact(
                    owner_id=owner_id,
                    subject_type="owner",
                    subject_id=owner_id,
                    action="owner.default_companion_changed",
                    payload={
                        # Both sides of the change. An event that says the
                        # pointer moved without saying what it moved to is one
                        # nobody can read — including the person whose Eidolon
                        # it is.
                        "companion_id": replacement.companion_id,
                        "previous_companion_id": companion_id,
                        "reason": "retirement",
                    },
                )
            )
        companion.lifecycle_state = "retiring"
        companion.revision += 1
        companion.updated_at = utc_now()
        session.add(
            governance_fact(
                owner_id=owner_id,
                subject_type="companion",
                subject_id=companion_id,
                action="companion.retirement_begun",
                payload={
                    "replacement_companion_id": (
                        replacement.companion_id if replacement else None
                    )
                },
            )
        )

    async def archive_companion(
        self,
        *,
        owner_id: str,
        companion_id: str,
        expected_revision: int | None = None,
    ) -> CompanionRow:
        """Put it away, once the things that had to stop have stopped.

        Only from ``retiring``. Refusing to archive an active Companion is the
        invariant that makes the workflow's order real rather than advisory: the
        steps that drain sessions and hand back Body assignments run between the
        two commands, and a caller that could skip straight here would skip them.

        Memory is deliberately untouched. A Realm belongs to the Owner and is
        read by every Companion they have, so archiving one of them releases
        nothing and deletes nothing (§4.4 of the isolation decision).
        """

        async with self._session_factory() as session, session.begin():
            companion = await _owned_companion(session, owner_id, companion_id)
            await self._archive(
                session,
                owner_id=owner_id,
                companion=companion,
                expected_revision=expected_revision,
            )
            return companion

    async def _archive(
        self,
        session,
        *,
        owner_id: str,
        companion: CompanionRow,
        expected_revision: int | None,
    ) -> None:
        """The archiving half, on a session someone else opened."""

        companion_id = companion.companion_id
        if companion.lifecycle_state == "archived":
            return
        _require_transition(companion, "archived", expected_revision)
        owner = await session.get(OwnerRow, owner_id)
        if owner is not None and owner.default_companion_id == companion_id:
            # Unreachable through retirement, which moves the pointer first.
            # Refused rather than quietly cleared: a pointer that survived
            # retirement means something wrote it in between, and archiving on
            # top of that would leave an Owner whose unaddressed requests go
            # nowhere.
            raise CompanionLifecycleConflict(
                "companion is still this owner's default",
                code="default_replacement_required",
            )
        companion.lifecycle_state = "archived"
        companion.revision += 1
        companion.updated_at = utc_now()
        session.add(
            governance_fact(
                owner_id=owner_id,
                subject_type="companion",
                subject_id=companion_id,
                action="companion.archived",
            )
        )

    async def put_away_companion(
        self,
        *,
        owner_id: str,
        companion_id: str,
        expected_revision: int | None = None,
        replacement_companion_id: str | None = None,
    ) -> CompanionRow:
        """Put it away: retire it and archive it, in one transaction.

        The two steps exist because something is meant to happen between them —
        sessions drain, Body assignments are handed back — and **nothing does
        yet**: no per-Companion drain primitive exists, and BodyAssignment does
        not exist at all. While that is true, making a caller issue two commands
        does not enforce the order, it only invents a state a person can get
        stuck in: a Host that dies between the two leaves a Companion
        ``retiring``, which is neither where they were nor where they asked to
        be.

        So the product action is one transaction, and ``retiring`` stops being
        observable through it. Both governance facts are still recorded — the
        record says the retirement happened, because it did.

        **When a step does appear between them, this command is the thing that
        must go**, and a coordinator uses ``begin_retirement`` and
        ``archive_companion`` around its own work. They are still here, still
        refuse an out-of-order move, and are still what a workflow would call.
        Deleting this one at that point is a smaller change than discovering
        that it silently skipped a drain.
        """

        async with self._session_factory() as session, session.begin():
            companion = await _owned_companion(session, owner_id, companion_id)
            if companion.lifecycle_state != "archived":
                await self._retire(
                    session,
                    owner_id=owner_id,
                    companion=companion,
                    expected_revision=expected_revision,
                    replacement_companion_id=replacement_companion_id,
                )
                # The revision moved in the half above, so the caller's is no
                # longer the one to compare against — it was already compared,
                # once, against the state this transaction started from.
                await self._archive(
                    session,
                    owner_id=owner_id,
                    companion=companion,
                    expected_revision=None,
                )
            return companion

    async def restore_companion(
        self,
        *,
        owner_id: str,
        companion_id: str,
        expected_revision: int | None = None,
    ) -> CompanionRow:
        """Bring it back, and nothing else.

        Deliberately does **not** make it the default again or return the Body
        assignments it had: those were given away to someone, and taking them
        back without being asked would move an Owner's Eidolon out from under
        whatever is using it now. Restoring says this Companion may answer again;
        what it answers through is a separate decision the Owner makes.
        """

        async with self._session_factory() as session, session.begin():
            companion = await _owned_companion(session, owner_id, companion_id)
            if companion.lifecycle_state == "active":
                return companion
            _require_transition(companion, "active", expected_revision)
            companion.lifecycle_state = "active"
            companion.revision += 1
            companion.updated_at = utc_now()
            session.add(
                governance_fact(
                    owner_id=owner_id,
                    subject_type="companion",
                    subject_id=companion_id,
                    action="companion.restored",
                    payload={"restored_from": "archived"},
                )
            )
            return companion

    async def set_default_companion(
        self,
        *,
        owner_id: str,
        companion_id: str,
        expected_revision: int | None = None,
    ) -> CompanionRow:
        """Point this Owner's unaddressed requests at this Companion.

        One write, to one field on the Owner. The old shape moved a ``role``
        between two Companion rows and needed a flush in the middle to get out
        of the way of a partial unique index — a two-row dance to express a
        one-place fact.

        Nothing else moves: existing sessions keep the Companion they were
        created with, Body assignments are untouched, and no memory is copied.
        Changing the default changes where *new* unaddressed work goes.
        """
        async with self._session_factory() as session, session.begin():
            companion = await _owned_active_companion(session, owner_id, companion_id)
            if companion.kind == "guard":
                raise OwnerWorkspaceError("a guard companion cannot be the default")
            owner = await session.get(OwnerRow, owner_id)
            if owner is None:
                raise OwnerWorkspaceNotFound("owner not found")
            previous_id = owner.default_companion_id
            if expected_revision is not None and expected_revision != owner.revision:
                # Stale view — unless the thing it was asking for is already
                # true. A retry after a lost response is the common case, and
                # refusing it would make the caller's only safe move a re-read
                # followed by another write that changes nothing.
                if previous_id != companion_id:
                    raise OwnerWorkspaceConflict(
                        "owner revision has moved since this caller read it"
                    )
            if previous_id != companion_id:
                owner.default_companion_id = companion_id
                owner.revision += 1
                owner.updated_at = utc_now()
                session.add(
                    governance_fact(
                        owner_id=owner_id,
                        subject_type="owner",
                        subject_id=owner_id,
                        action="owner.default_companion_changed",
                        payload={
                            "companion_id": companion_id,
                            "previous_companion_id": previous_id,
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

    async def provision_companion(
        self,
        *,
        owner_id: str,
        operation_id: str,
        request_fingerprint: str,
        companion_display_name: str,
        kind: str = "conversational",
        persona: PersonaAuthoring | None = None,
    ) -> CompanionProvisionResult:
        """Add a Companion to an Owner who already has one, exactly once.

        Idempotent the same way onboarding is, and for the same reason: the
        caller is a phone, the answer can be lost, and the only safe retry is
        one that cannot create a second Companion. Every identifier is derived
        from the operation id, so a retry addresses the same rows; the request
        fingerprint is stored on the Companion as provenance, so a *different*
        request reusing an operation id is a conflict rather than a silent
        overwrite. No second workflow table is involved.

        The Owner's memory realm is not created per Companion (§4.4). A second
        Companion shares the one its Owner already has, which is why this can
        report ``memory_realm_created=False`` — and why the derived realm id is
        used only when there was no realm at all.

        ``persona`` is who this Eidolon starts out as, in the person's own
        words. Absent, it is the template — which is what every Companion got
        for a while after the authoring screen was removed, meaning two
        Companions differed only by name. The genome is built *here* rather than
        by the caller because this is the authority that owns personas: a
        management layer composing a genome would be a second place that decides
        what an unauthored Eidolon is.
        """

        owner_id = _validate_owner_id(owner_id)
        canonical_operation_id = _validate_operation_id(operation_id)
        if not REQUEST_FINGERPRINT_RE.fullmatch(request_fingerprint):
            raise OwnerWorkspaceError("request_fingerprint must be a sha256 digest")
        if kind not in COMPANION_KINDS:
            raise OwnerWorkspaceError(
                "kind must be conversational, guard, specialist, or system"
            )
        display_name = companion_display_name.strip()
        if not display_name:
            raise OwnerWorkspaceError("companion_display_name cannot be blank")
        ids = _provision_ids(canonical_operation_id)

        async with self._session_factory() as session, session.begin():
            existing = await session.get(CompanionRow, ids["companion_id"])
            if existing is not None:
                return await _load_provision_result(
                    session,
                    owner_id=owner_id,
                    operation_id=canonical_operation_id,
                    request_fingerprint=request_fingerprint,
                )
            realm_before = await _active_realm_for_owner(session, owner_id)
            result = await _provision_workspace_in_session(
                session,
                owner_id=owner_id,
                companion_id=ids["companion_id"],
                companion_display_name=display_name,
                kind=kind,
                companion_profile_json=None,
                companion_runtime_config_json=None,
                companion_metadata_json={
                    "source": "companion_provision",
                    PROVISION_METADATA_KEY: {
                        "operation_id": canonical_operation_id,
                        "request_fingerprint": request_fingerprint,
                    },
                },
                genome_id=ids["genome_id"],
                genome_source_json={
                    # A genome somebody wrote and a genome the Host defaulted to
                    # are different facts, and the record has to be able to tell
                    # them apart long after both look like text in a column.
                    "source_type": (
                        "owner_authored" if persona is not None else "companion_provision"
                    ),
                    "owner_id": owner_id,
                    "operation_id": canonical_operation_id,
                },
                genome_json=persona_genome_to_json(
                    build_persona_genome_from_draft(
                        PersonaAuthoringDraft.for_companion(
                            persona, name=display_name
                        ),
                        origin=(
                            "owner_authored" if persona is not None else "template"
                        ),
                    )
                ),
                realm_id=ids["realm_id"],
                memory_engine="mempalace",
                memory_engine_config_json=None,
                memory_policy_json=None,
            )
            return CompanionProvisionResult(
                operation_id=canonical_operation_id,
                request_fingerprint=request_fingerprint,
                companion=result.companion,
                persona_genome=result.persona_genome,
                memory_realm=result.memory_realm,
                memory_realm_created=realm_before is None,
                replayed=False,
            )

    async def provision_workspace(
        self,
        *,
        owner_id: str,
        companion_id: str | None = None,
        companion_display_name: str = "",
        kind: str = "conversational",
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
        if kind not in COMPANION_KINDS:
            raise OwnerWorkspaceError(
                "kind must be conversational, guard, specialist, or system"
            )
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
                kind=kind,
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
    kind: str,
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

    companion_name = companion_display_name.strip() or f"{owner.display_name or owner_id} Companion"
    companion = CompanionRow(
        companion_id=companion_id,
        owner_id=owner_id,
        display_name=companion_name,
        kind=kind,
        lifecycle_state="active",
        profile_json=dict(companion_profile_json or {}),
        runtime_config_json=dict(companion_runtime_config_json or {}),
        metadata_json=dict(companion_metadata_json or {}),
    )
    session.add(companion)
    await session.flush()
    if owner.default_companion_id is None and kind != "guard":
        # The Owner's first default-eligible Companion becomes the default. It
        # is set here rather than left to a second call because an Owner with a
        # Companion and no default has no answer for an unaddressed request,
        # and nothing downstream can invent one.
        owner.default_companion_id = companion.companion_id
        owner.revision += 1
        owner.updated_at = utc_now()

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
            "kind": kind,
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
        or owner.default_companion_id != companion.companion_id
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


async def _owned_companion(session, owner_id: str, companion_id: str) -> CompanionRow:
    """This Owner's Companion in whatever state it is in.

    Separate from :func:`_owned_active_companion` because the lifecycle commands
    are the ones that act on a Companion that is *not* active, and reusing the
    active-only helper would make "already retiring" indistinguishable from "not
    yours".
    """

    companion = await session.get(CompanionRow, companion_id)
    if companion is None or companion.owner_id != owner_id:
        raise CompanionLifecycleConflict(
            "companion not found for owner", code="not_found"
        )
    return companion


def _require_transition(
    companion: CompanionRow, target: str, expected_revision: int | None
) -> None:
    """Refuse a move the lifecycle does not allow, or a stale caller.

    Order matters: the transition is checked first. A caller holding an old
    revision *and* asking for an impossible move should be told the move is
    impossible — re-reading and trying again would not help.
    """

    allowed = COMPANION_LIFECYCLE_TRANSITIONS.get(companion.lifecycle_state, ())
    if target not in allowed:
        raise CompanionLifecycleConflict(
            f"a {companion.lifecycle_state} companion cannot become {target}",
            code="transition_not_allowed",
        )
    if expected_revision is not None and expected_revision != companion.revision:
        raise CompanionLifecycleConflict(
            "companion revision has moved since this caller read it",
            code="revision_stale",
        )


async def _eligible_replacement(
    session,
    *,
    owner_id: str,
    retiring_id: str,
    replacement_companion_id: str | None,
) -> CompanionRow:
    """Who answers instead, or a refusal that says which question to ask.

    Two different refusals on purpose. "Name a replacement" is a question for the
    person and their answer resolves it; "there is nobody else" is not a question
    at all, and offering them a picker with one unusable entry would be worse
    than saying so.
    """

    others = (
        await session.scalars(
            select(CompanionRow).where(
                CompanionRow.owner_id == owner_id,
                CompanionRow.companion_id != retiring_id,
                CompanionRow.lifecycle_state.in_(DEFAULT_ELIGIBLE_LIFECYCLE_STATES),
                CompanionRow.kind != "guard",
            )
        )
    ).all()
    if not others:
        raise CompanionLifecycleConflict(
            "this owner has no other companion that could answer",
            code="last_active_companion",
        )
    if replacement_companion_id is None:
        raise CompanionLifecycleConflict(
            "archiving the default companion requires a replacement",
            code="default_replacement_required",
        )
    for candidate in others:
        if candidate.companion_id == replacement_companion_id:
            return candidate
    raise CompanionLifecycleConflict(
        "the named replacement cannot answer for this owner",
        code="default_replacement_ineligible",
    )


async def _owned_active_companion(session, owner_id: str, companion_id: str) -> CompanionRow:
    companion = await session.get(CompanionRow, companion_id)
    if companion is None or companion.owner_id != owner_id:
        raise OwnerWorkspaceNotFound("companion not found for owner")
    if companion.lifecycle_state != "active":
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


def _provision_ids(operation_id: str) -> dict[str, str]:
    """Every identifier a provision touches, derived from its operation id.

    Prefixed apart from the onboarding ids so the two events cannot land on the
    same rows even if a caller reused a uuid across both.
    """
    operation_hex = UUID(operation_id).hex
    return {
        "companion_id": f"cp_{operation_hex}",
        "genome_id": f"gp_{operation_hex}_origin",
        #: Used only when the Owner has no realm yet. A second Companion shares
        #: the Owner's one realm, so this normally goes unused.
        "realm_id": f"rp_{operation_hex}",
    }


async def _active_realm_for_owner(session, owner_id: str):
    from sqlalchemy import select

    return (
        await session.execute(
            select(MemoryRealmRow).where(
                MemoryRealmRow.owner_id == owner_id,
                #: The column is ``status`` here, not ``lifecycle_state``: this
                #: table names it that way and renaming it is not this change's
                #: business.
                MemoryRealmRow.status == "active",
            )
        )
    ).scalars().first()


async def _load_provision_result(
    session,
    *,
    owner_id: str,
    operation_id: str,
    request_fingerprint: str,
) -> CompanionProvisionResult:
    """Reconstruct a completed provision from the rows it created.

    Nothing is stored about the operation except what the Companion carries, so
    "has this been done" is answered by the same rows the caller asked about —
    there is no second place that could disagree with them.
    """

    ids = _provision_ids(operation_id)
    companion = await session.get(CompanionRow, ids["companion_id"])
    genome = await session.get(PersonaGenomeRow, ids["genome_id"])
    if companion is None or genome is None:
        raise OwnerWorkspaceError("companion provision resources are incomplete")
    if companion.owner_id != owner_id:
        # The operation id belongs to someone else's Companion. Absent rather
        # than forbidden, so an operation id cannot be probed across Owners.
        raise OwnerWorkspaceNotFound("companion provision not found for owner")
    metadata = (companion.metadata_json or {}).get(PROVISION_METADATA_KEY)
    if not isinstance(metadata, dict) or metadata.get("operation_id") != operation_id:
        raise OwnerWorkspaceConflict("operation_id belongs to another operation")
    if metadata.get("request_fingerprint") != request_fingerprint:
        raise OwnerWorkspaceConflict("operation_id is already in use for another request")
    realm = await _active_realm_for_owner(session, owner_id)
    if realm is None:
        raise OwnerWorkspaceError("companion provision resources are incomplete")
    return CompanionProvisionResult(
        operation_id=operation_id,
        request_fingerprint=request_fingerprint,
        companion=companion,
        persona_genome=genome,
        memory_realm=realm,
        # A replay creates nothing, so it never asks for a runtime signal. The
        # signal for the original create was that caller's to send.
        memory_realm_created=False,
        replayed=True,
    )


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
