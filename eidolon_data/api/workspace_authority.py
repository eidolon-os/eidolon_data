"""Versioned command authority for first Owner workspace initialization."""

from __future__ import annotations

import asyncio
import hashlib
import json
from contextlib import asynccontextmanager, suppress
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

from eidolon_sdk.biz.contracts.companion import CompanionLifecycleState
from eidolon_data import DataSettings, DataStore, load_settings
from eidolon_data.audit import run_audit_dispatcher
from eidolon_data.services.owner_workspace import (
    CompanionLifecycleConflict,
    OwnerWorkspaceConflict,
    OwnerWorkspaceError,
    OwnerWorkspaceInitializationResult,
    OwnerWorkspaceNotFound,
)

from .service_auth import authorize_service, required_service_token


class WorkspaceInitializeRequest(BaseModel):
    """Minimal first-use input; later persona editing is a separate product flow."""

    model_config = ConfigDict(extra="forbid")

    owner_display_name: str = Field(min_length=1, max_length=128)
    companion_display_name: str = Field(default="Eidolon", min_length=1, max_length=128)


class OwnerRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=128)


class CompanionProvisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    companion_display_name: str = Field(min_length=1, max_length=128)
    kind: str = Field(default="conversational", min_length=1, max_length=32)


class ProvisionedCompanionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    companion_id: str
    display_name: str
    kind: str
    lifecycle_state: CompanionLifecycleState
    revision: int = Field(ge=1)


class CompanionProvisionResponse(BaseModel):
    """What one provision produced, identically on a retry."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["companion.provision"] = "companion.provision"
    operation_id: str
    request_fingerprint: str
    companion: ProvisionedCompanionResponse
    persona_genome_id: str
    memory_realm_id: str
    #: True only when this call catalogued the Owner's first realm. A second
    #: Companion shares the Owner's memory (§4.4), so this is normally false —
    #: and it is what tells a caller whether any runtime reconcile is owed.
    memory_realm_created: bool
    #: True when this operation had already been carried out. The body is the
    #: same either way; a caller that must not act twice reads this.
    replayed: bool


class DefaultCompanionRequest(BaseModel):
    """Which Companion answers when nothing named one."""

    model_config = ConfigDict(extra="forbid")

    companion_id: str = Field(min_length=1, max_length=64)
    #: The Owner revision this caller last read. Optional so an internal caller
    #: with no prior read is not forced to invent one, required in practice by
    #: the management boundary, which always has just read it.
    expected_revision: int | None = Field(default=None, ge=1)


class CompanionLifecycleRequest(BaseModel):
    """Where this Companion should be in its life.

    A desired end rather than a step, which is what lets a client that never saw
    the answer send it again.
    """

    model_config = ConfigDict(extra="forbid")

    #: The ownership boundary. In the body rather than the path because it is not
    #: what this resource is — the Companion is — and a route keyed on both would
    #: invite a caller to think the pair is the identity.
    owner_id: str = Field(min_length=1, max_length=64)
    #: ``deleting`` is not offered: hard deletion is a separate data-governance
    #: workflow, and a lifecycle route that could reach it would make the two one
    #: button apart.
    lifecycle_state: Literal["retiring", "archived", "active"]
    expected_revision: int | None = Field(default=None, ge=1)
    #: Required when retiring the Companion an Owner's unaddressed requests go
    #: to, ignored otherwise. In the same request because between two there would
    #: be an Owner whose Eidolon cannot answer.
    replacement_companion_id: str | None = Field(default=None, min_length=1, max_length=64)


class CompanionLifecycleResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["companion.lifecycle"] = "companion.lifecycle"
    companion_id: str = Field(min_length=1, max_length=64)
    lifecycle_state: CompanionLifecycleState
    revision: int = Field(ge=1)
    #: Who answers for this Owner now. ``None`` when nobody does.
    default_companion_id: str | None = Field(default=None, max_length=64)


class GovernanceEventResponse(BaseModel):
    """One thing that happened to this Owner's things.

    Facts, not sentences. What a person reads is composed where the words live;
    what this authority knows is what happened, to what, and when.
    """

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=64)
    #: Stable machine word (``companion.archived``,
    #: ``owner.default_companion_changed``). A consumer that has never heard of
    #: one must still show that it happened.
    action: str = Field(min_length=1, max_length=128)
    subject_type: str = Field(min_length=1, max_length=64)
    subject_id: str = Field(min_length=1, max_length=128)
    outcome: str = Field(min_length=1, max_length=16)
    severity: str = Field(min_length=1, max_length=16)
    occurred_at: str = Field(min_length=1, max_length=64)
    #: Carried only for events this authority classified as safe to show. A
    #: classified payload is dropped and the event is still reported: hiding
    #: that something happened is a bigger lie than not saying what was in it.
    payload: dict = Field(default_factory=dict)


class OwnerGovernanceEventsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["owner.governance-events"] = "owner.governance-events"
    owner_id: str = Field(min_length=1, max_length=64)
    #: Newest first.
    events: list[GovernanceEventResponse]
    #: Send back to read the page before this one. ``None`` means this is as far
    #: back as the Host still holds — not that nothing happened before.
    next_cursor: int | None = Field(default=None, ge=1)


class OwnerIdentityResponse(BaseModel):
    """Stable identity subset consumed by OS control-plane services."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["owner.identity"] = "owner.identity"
    owner_id: str
    display_name: str
    lifecycle_state: Literal["active", "archived", "deleting"]
    #: Which Companion answers when nothing named one. An Owner aggregate field,
    #: so it is read here rather than derived from a Companion list — and it is
    #: the only place that says it. ``None`` is a real state: the Owner has no
    #: default-eligible Companion, and no caller may resolve that by choosing.
    default_companion_id: str | None = Field(default=None, max_length=64)
    #: Owner aggregate version. Setting the default writes the Owner, so this is
    #: the value a writer compares against.
    revision: int = Field(ge=1)


class OwnerResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_id: str
    display_name: str
    lifecycle_state: Literal["active"] = "active"


class WorkspaceResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: Literal["ready"] = "ready"
    primary_companion_id: str
    persona_genome_id: str
    memory_realm_id: str


class WorkspaceOperationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["owner-workspace.initialize"] = "owner-workspace.initialize"
    operation_id: str
    request_fingerprint: str
    status: Literal["succeeded"] = "succeeded"
    owner: OwnerResult
    workspace: WorkspaceResult


def create_app(
    settings: DataSettings | None = None,
    *,
    service_token: str | None = None,
) -> FastAPI:
    """Create the write authority without mounting legacy CRUD routes."""

    token = required_service_token(
        service_token,
        environment_name="EIDOLON_DATA_WORKSPACE_AUTHORITY_TOKEN",
    )
    store = DataStore.open(settings or load_settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await store.validate_schema()
        # One dispatcher, in the process that writes the facts. This authority
        # serves two apps over one database, and two loops draining one outbox
        # would publish the same events twice — harmlessly, because the
        # transport de-duplicates on event id, and pointlessly.
        #
        # No URL means no dispatcher *and no purge*: an unpublished governance
        # fact is still readable by its Owner, so a Host without a bus keeps its
        # whole history rather than losing it quietly.
        dispatcher: asyncio.Task | None = None
        if store.settings.audit_nats_url:
            dispatcher = asyncio.create_task(
                run_audit_dispatcher(
                    store.audit_outbox,
                    nats_url=store.settings.audit_nats_url,
                ),
                name="eidolon-data-audit-dispatcher",
            )
        try:
            yield
        finally:
            if dispatcher is not None:
                dispatcher.cancel()
                with suppress(asyncio.CancelledError):
                    await dispatcher
            await store.close()

    app = FastAPI(
        title="Eidolon Workspace Authority",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.get("/health", tags=["operations"])
    async def health() -> dict[str, str]:
        return {"status": "ready"}

    @app.put(
        "/api/workspace-authority/v1/operations/{operation_id}",
        response_model=WorkspaceOperationResponse,
        tags=["workspace-authority"],
    )
    async def initialize_workspace(
        operation_id: UUID,
        payload: WorkspaceInitializeRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> WorkspaceOperationResponse:
        authorize_service(authorization, token)
        fingerprint = _request_fingerprint(payload)
        try:
            result = await store.companion_workspaces.initialize_owner_workspace(
                operation_id=str(operation_id),
                request_fingerprint=fingerprint,
                owner_display_name=payload.owner_display_name,
                companion_display_name=payload.companion_display_name,
            )
        except OwnerWorkspaceError as exc:
            raise HTTPException(status_code=_workspace_error_status(exc), detail=str(exc)) from exc
        return _response(result)

    @app.get(
        "/api/workspace-authority/v1/operations/{operation_id}",
        response_model=WorkspaceOperationResponse,
        tags=["workspace-authority"],
    )
    async def get_workspace_operation(
        operation_id: UUID,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> WorkspaceOperationResponse:
        authorize_service(authorization, token)
        try:
            result = await store.companion_workspaces.get_owner_workspace_initialization(
                str(operation_id)
            )
        except OwnerWorkspaceError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="workspace operation not found")
        return _response(result)

    @app.get(
        "/api/workspace-authority/v1/owners/{owner_id}",
        response_model=OwnerIdentityResponse,
        tags=["workspace-authority"],
    )
    async def get_owner_identity(
        owner_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> OwnerIdentityResponse:
        authorize_service(authorization, token)
        row = await store.owners.get(owner_id)
        if row is None:
            raise HTTPException(status_code=404, detail="owner not found")
        return _owner_identity(row)

    @app.get(
        "/api/workspace-authority/v1/owners/{owner_id}/governance-events",
        response_model=OwnerGovernanceEventsResponse,
        tags=["workspace-authority"],
    )
    async def list_owner_governance_events(
        owner_id: str,
        limit: int = Query(default=50, ge=1, le=100),
        before: int | None = Query(default=None, ge=1),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> OwnerGovernanceEventsResponse:
        """What has happened to this Owner's things, newest first.

        The governance facts this authority already writes in the same
        transaction as the change itself — which is what makes them a record
        rather than a log: an event exists exactly when the thing it describes
        happened.

        **Recent, not complete.** These live in the audit outbox, whose
        retention is decided by whoever purges published rows. Nothing purges
        today; when something does, this window shrinks with it, and the name
        says so.
        """

        authorize_service(authorization, token)
        if await store.owners.get(owner_id) is None:
            raise HTTPException(status_code=404, detail="owner not found")
        page = await store.audit_outbox.list_for_owner(
            owner_id, limit=limit, before_sequence=before
        )
        return OwnerGovernanceEventsResponse(
            owner_id=owner_id,
            events=[
                GovernanceEventResponse(
                    event_id=event.event_id,
                    action=event.action,
                    subject_type=event.subject_type,
                    subject_id=event.subject_id,
                    outcome=event.outcome,
                    severity=event.severity,
                    occurred_at=event.occurred_at.isoformat(),
                    payload=(
                        dict(event.payload)
                        if event.data_classification == "safe"
                        else {}
                    ),
                )
                for event in page.events
            ],
            next_cursor=page.next_sequence,
        )

    @app.put(
        "/api/workspace-authority/v1/owners/{owner_id}/companion-provisions/{operation_id}",
        response_model=CompanionProvisionResponse,
        tags=["workspace-authority"],
    )
    async def provision_companion(
        owner_id: str,
        operation_id: UUID,
        payload: CompanionProvisionRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionProvisionResponse:
        """Add a Companion to this Owner, exactly once per operation id.

        The operation id is in the path and every identifier is derived from it,
        so a retry addresses the same rows rather than creating a second
        Companion. Reusing an id for a *different* request is a conflict, not an
        overwrite: the request fingerprint is stored as provenance and compared.
        """

        authorize_service(authorization, token)
        fingerprint = _provision_fingerprint(payload)
        try:
            result = await store.companion_workspaces.provision_companion(
                owner_id=owner_id,
                operation_id=str(operation_id),
                request_fingerprint=fingerprint,
                companion_display_name=payload.companion_display_name,
                kind=payload.kind,
            )
        except OwnerWorkspaceError as exc:
            raise HTTPException(
                status_code=_workspace_error_status(exc), detail=str(exc)
            ) from exc
        return CompanionProvisionResponse(
            operation_id=result.operation_id,
            request_fingerprint=result.request_fingerprint,
            companion=ProvisionedCompanionResponse(
                companion_id=result.companion.companion_id,
                display_name=result.companion.display_name,
                kind=result.companion.kind,
                lifecycle_state=result.companion.lifecycle_state,
                revision=result.companion.revision,
            ),
            persona_genome_id=result.persona_genome.genome_id,
            memory_realm_id=result.memory_realm.realm_id,
            memory_realm_created=result.memory_realm_created,
            replayed=result.replayed,
        )

    @app.put(
        "/api/workspace-authority/v1/owners/{owner_id}/default-companion",
        response_model=OwnerIdentityResponse,
        tags=["workspace-authority"],
    )
    async def set_default_companion(
        owner_id: str,
        payload: DefaultCompanionRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> OwnerIdentityResponse:
        """Point this Owner's unaddressed work at one of their Companions.

        PUT because it states a desired end, not a step: sending it twice leaves
        the same Owner pointing at the same Companion. That is what makes a
        retry after a lost response safe, and a retry after a lost response is
        the normal case on a phone.

        ``expected_revision`` is the Owner revision the caller last read. A
        stale one is a 409 — unless the Companion it names is already the
        default, which is the lost-response retry and is answered as success.
        Nothing else moves: existing sessions keep the Companion they were
        created with, Body assignments are untouched, no memory is copied.
        """

        authorize_service(authorization, token)
        try:
            await store.companion_workspaces.set_default_companion(
                owner_id=owner_id,
                companion_id=payload.companion_id,
                expected_revision=payload.expected_revision,
            )
        except OwnerWorkspaceError as exc:
            raise HTTPException(
                status_code=_workspace_error_status(exc), detail=str(exc)
            ) from exc
        row = await store.owners.get(owner_id)
        if row is None:
            raise HTTPException(status_code=404, detail="owner not found")
        return _owner_identity(row)

    @app.put(
        "/api/workspace-authority/v1/companions/{companion_id}/lifecycle",
        response_model=CompanionLifecycleResponse,
        tags=["workspace-authority"],
    )
    async def set_companion_lifecycle(
        companion_id: str,
        payload: CompanionLifecycleRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionLifecycleResponse:
        """Move a Companion along its lifecycle: retiring, archived, or back.

        One route for the three moves rather than three verbs, because the body
        states a desired end and the authority decides whether it is reachable
        from where the Companion is. That is also what makes a retry after a lost
        answer safe: asking for the state it is already in succeeds and records
        nothing twice.

        ``owner_id`` is required and is the ownership boundary — a Companion that
        is not this Owner's is 404, the same answer one that does not exist gets.
        The refusals carry a ``code`` because they lead a person to different next
        moves: a stale revision is worth a re-read, "name a replacement" is a
        question, and "there is nobody else" is a refusal to leave an Owner with
        no Eidolon at all.
        """

        authorize_service(authorization, token)
        commands = {
            "retiring": store.companion_workspaces.begin_retirement,
            # Reaching ``archived`` from ``active`` is one transaction, not two
            # requests. Nothing runs between the two halves today — no drain
            # exists, and BodyAssignment does not exist at all — so asking a
            # caller to make both calls would not enforce the order, it would
            # only leave a Companion stuck in ``retiring`` when a Host dies in
            # between. ``begin_retirement`` above is still here for the
            # coordinator that will have work to do in the middle.
            "archived": store.companion_workspaces.put_away_companion,
            "active": store.companion_workspaces.restore_companion,
        }
        arguments: dict[str, object] = {
            "owner_id": payload.owner_id,
            "companion_id": companion_id,
            "expected_revision": payload.expected_revision,
        }
        if payload.lifecycle_state in {"retiring", "archived"}:
            arguments["replacement_companion_id"] = payload.replacement_companion_id
        try:
            row = await commands[payload.lifecycle_state](**arguments)
        except CompanionLifecycleConflict as exc:
            raise HTTPException(
                status_code=_workspace_error_status(exc),
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        except OwnerWorkspaceError as exc:
            raise HTTPException(
                status_code=_workspace_error_status(exc), detail=str(exc)
            ) from exc
        owner = await store.owners.get(payload.owner_id)
        return CompanionLifecycleResponse(
            companion_id=row.companion_id,
            lifecycle_state=row.lifecycle_state,
            revision=row.revision,
            # Which Companion answers now. Carried because the caller that just
            # retired a default needs it, and asking for it separately would be a
            # second read of a fact this transaction already settled.
            default_companion_id=owner.default_companion_id if owner else None,
        )

    @app.patch(
        "/api/workspace-authority/v1/owners/{owner_id}",
        response_model=OwnerIdentityResponse,
        tags=["workspace-authority"],
    )
    async def rename_owner(
        owner_id: str,
        payload: OwnerRenameRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> OwnerIdentityResponse:
        """The one thing about an Owner its own person may set directly.

        This authority is where an Owner is born, so it is also where the name
        given at that moment is corrected. It stays closed to everything but
        Admin, and whether the caller *is* this Owner is decided at the Local
        API boundary, where an Owner's authority is known — deciding it twice
        would mean deciding it differently one day.
        """

        authorize_service(authorization, token)
        display_name = payload.display_name.strip()
        if not display_name:
            raise HTTPException(status_code=422, detail="display_name cannot be blank")
        row = await store.owners.rename(owner_id, display_name)
        if row is None:
            raise HTTPException(status_code=404, detail="owner not found")
        return _owner_identity(row)

    return app


def _owner_identity(row) -> OwnerIdentityResponse:
    return OwnerIdentityResponse(
        owner_id=row.owner_id,
        display_name=row.display_name,
        lifecycle_state=row.status,
        default_companion_id=row.default_companion_id,
        revision=row.revision,
    )


def _provision_fingerprint(payload: CompanionProvisionRequest) -> str:
    """The request's content, canonically, so a retry hashes the same.

    Same construction as the onboarding fingerprint. It is compared, never
    parsed: its only job is to tell "this request again" from "a different
    request wearing the same operation id".
    """
    canonical = json.dumps(
        payload.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _request_fingerprint(payload: WorkspaceInitializeRequest) -> str:
    canonical = json.dumps(
        payload.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _response(result: OwnerWorkspaceInitializationResult) -> WorkspaceOperationResponse:
    workspace = result.workspace
    return WorkspaceOperationResponse(
        operation_id=result.operation_id,
        request_fingerprint=result.request_fingerprint,
        owner=OwnerResult(
            owner_id=result.owner.owner_id,
            display_name=result.owner.display_name,
        ),
        workspace=WorkspaceResult(
            primary_companion_id=workspace.companion.companion_id,
            persona_genome_id=workspace.persona_genome.genome_id,
            memory_realm_id=workspace.memory_realm.realm_id,
        ),
    )


def _workspace_error_status(error: OwnerWorkspaceError) -> int:
    if isinstance(error, CompanionLifecycleConflict):
        # ``not_found`` is one code covering "not there" and "not yours", so an
        # id cannot be probed; everything else a lifecycle command refuses is a
        # conflict about state the caller can re-read.
        return 404 if error.code == "not_found" else 409
    #: Typed first. The substring checks below predate the typed errors and are
    #: kept only for the paths that still raise the base class; a status decided
    #: by reading a sentence changes when someone rewords the sentence.
    if isinstance(error, OwnerWorkspaceNotFound):
        return 404
    if isinstance(error, OwnerWorkspaceConflict):
        return 409
    message = str(error)
    if "already in use" in message or "belong to another" in message:
        return 409
    if "incomplete" in message or "inconsistent" in message:
        return 409
    return 400
