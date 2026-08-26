"""Versioned, least-privilege Companion identity authority service."""

from __future__ import annotations

import base64
import binascii
import hashlib
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from eidolon_sdk.biz.contracts.companion import CompanionLifecycleState
from eidolon_sdk.biz.persona import (
    PersonaAuthoring,
    normalize_persona_genome,
    persona_authoring_of,
)
from eidolon_data import DataSettings, DataStore, load_settings
from eidolon_data.repositories.persona import PersonaGenomeConflict

from .service_auth import authorize_service, required_service_token


#: A face is a photograph, not a document. Larger than this is not a portrait
#: of an Eidolon; it is a file someone picked by accident.
MAXIMUM_FACE_BYTES = 8 * 1024 * 1024

#: The first three bytes of every JPEG.
JPEG_MAGIC = b"\xff\xd8\xff"


class CompanionFaceResponse(BaseModel):
    """What is known about this Companion's face, without carrying it."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["companion.face"] = "companion.face"
    companion_id: str = Field(min_length=1, max_length=64)
    has_face: bool
    face_asset_id: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    updated_at: str | None = None


class CompanionIdentityResponse(BaseModel):
    """Stable identity subset consumed by OS control-plane services."""

    model_config = ConfigDict(extra="forbid")

    operation: Literal["companion.identity"] = "companion.identity"
    companion_id: str = Field(min_length=1, max_length=64)
    owner_id: str = Field(min_length=1, max_length=64)
    #: What the Owner calls this Eidolon. Written at onboarding and, until now,
    #: never read back — the product showed an identifier where a person had
    #: given it a name.
    display_name: str = Field(default="", max_length=128)
    #: Straight from the column. It used to be folded into two values here,
    #: which meant a consumer could not tell "the Owner archived it" from
    #: "it cannot run right now" — and that conflation is what the identity
    #: schema was changed to remove.
    lifecycle_state: CompanionLifecycleState
    #: The product type, independent of which Companion is the default.
    kind: str = Field(min_length=1, max_length=32)
    #: Aggregate version, for compare-and-swap on writes.
    revision: int = Field(ge=1)


class PersonaChapterResponse(BaseModel):
    """One thing this Companion has been, and why it changed.

    The version and hash are here because an authority answers precisely, not
    because anyone should be shown them. What a person reads is when it
    changed and what changed — `change_summary`, which the Companion writes
    about itself.
    """

    model_config = ConfigDict(extra="forbid")

    genome_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    lifecycle_state: Literal["committed", "proposed", "rejected", "stale"]
    change_summary: str = Field(default="", max_length=4096)
    restored_from_version: int | None = None
    is_current: bool = False
    created_at: str


class PersonaTimelineResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    operation: Literal["companion.persona-timeline"] = "companion.persona-timeline"
    companion_id: str = Field(min_length=1, max_length=64)
    chapters: list[PersonaChapterResponse] = Field(default_factory=list)


class PersonaRestoreRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    genome_id: str = Field(min_length=1, max_length=64)
    change_summary: str = Field(default="", max_length=4096)


class PersonaAuthoringRequest(BaseModel):
    """Who this Companion is now, and one line about why it changed.

    The persona travels as the SDK's own shape rather than a copy of it, so what
    a screen reads, what it sends and what gets built are one thing — and a field
    added to the genome cannot go missing between them.
    """

    model_config = ConfigDict(extra="forbid")

    persona: PersonaAuthoring
    #: What a person reads later when they wonder what happened. Written by
    #: whoever made the change and stored as written: a sentence about who
    #: somebody's Eidolon became should not be composed by a projection.
    change_summary: str = Field(default="", max_length=4096)


class CompanionRenameRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    display_name: str = Field(min_length=1, max_length=128)


class MemoryRealmSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    realm_id: str = Field(min_length=1, max_length=64)
    lifecycle_state: Literal["active"] = "active"


class PersonaGenomeSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    genome_id: str = Field(min_length=1, max_length=64)
    version: int = Field(ge=1)
    lifecycle_state: Literal["committed"] = "committed"
    schema_version: str = Field(min_length=1, max_length=64)
    genome_hash: str = Field(min_length=1, max_length=80)
    realizer_version: str = Field(min_length=1, max_length=64)
    genome: dict[str, Any]


class CompanionRuntimeSnapshotResponse(BaseModel):
    """Ready-to-run Companion facts owned by System Data."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["companion.runtime-snapshot"] = "companion.runtime-snapshot"
    owner_id: str = Field(min_length=1, max_length=64)
    companion_id: str = Field(min_length=1, max_length=64)
    lifecycle_state: Literal["active"] = "active"
    runtime_config: dict[str, Any]
    memory_realm: MemoryRealmSnapshot
    persona_genome: PersonaGenomeSnapshot


class CompanionSummaryResponse(BaseModel):
    """One Companion as a roster row: the three identity axes and its name.

    Deliberately without ``is_default``. Which Companion an Owner falls back to
    is one field on the Owner, and a boolean repeated on every row would be a
    second place saying it — with "two rows both claim it" as a representable
    state. The page carries the pointer once; a reader compares.
    """

    model_config = ConfigDict(extra="forbid")

    companion_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(default="", max_length=128)
    kind: str = Field(min_length=1, max_length=32)
    lifecycle_state: CompanionLifecycleState
    revision: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class CompanionPageResponse(BaseModel):
    """This Owner's Companions, oldest first, with the default named once."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["companion.roster-page"] = "companion.roster-page"
    owner_id: str = Field(min_length=1, max_length=64)
    #: The Owner's pointer, verbatim. ``None`` means this Owner has no default —
    #: a real state (their only Companion is a guard, or the default was
    #: archived), and one a caller must not paper over by choosing a row.
    default_companion_id: str | None = Field(default=None, max_length=64)
    companions: list[CompanionSummaryResponse]
    #: Opaque. A caller stores and returns it; parsing it would make the page
    #: boundary part of the contract.
    next_cursor: str | None = Field(default=None, max_length=256)


class MemoryRuntimeRealm(BaseModel):
    """One active Memory Realm and the authority facts needed to run it."""

    model_config = ConfigDict(extra="forbid")

    realm_id: str = Field(min_length=1, max_length=64)
    owner_id: str = Field(min_length=1, max_length=64)
    engine: str = Field(min_length=1, max_length=64)
    engine_config: dict[str, Any]


class MemoryRuntimeRosterResponse(BaseModel):
    """Active Memory runtime roster projected by the System Data authority."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["memory.runtime-roster"] = "memory.runtime-roster"
    realms: list[MemoryRuntimeRealm]


ROSTER_PAGE_LIMIT = 50


def _encode_cursor(row) -> str:
    raw = f"{row.created_at.isoformat()}\x1f{row.companion_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, str]:
    """A cursor the server issued, or a refusal.

    Silently restarting from the beginning on a cursor we cannot read would
    hand a caller a page it has already seen and call it progress.
    """
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        created_at, _, companion_id = (
            base64.urlsafe_b64decode(padded).decode().partition("\x1f")
        )
        if not companion_id:
            raise ValueError("cursor is missing its tiebreak")
        return datetime.fromisoformat(created_at), companion_id
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=422, detail="cursor is not readable") from exc


def create_app(
    settings: DataSettings | None = None,
    *,
    service_token: str | None = None,
    memory_roster_token: str | None = None,
) -> FastAPI:
    """Create the narrow authority app; legacy Data CRUD routes are not mounted."""

    token = required_service_token(
        service_token,
        environment_name="EIDOLON_DATA_COMPANION_AUTHORITY_TOKEN",
    )
    roster_token = required_service_token(
        memory_roster_token,
        environment_name="EIDOLON_DATA_MEMORY_RUNTIME_ROSTER_TOKEN",
    )
    store = DataStore.open(settings or load_settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        await store.validate_schema()
        try:
            yield
        finally:
            await store.close()

    app = FastAPI(
        title="Eidolon Companion Authority",
        version="1.0.0",
        lifespan=lifespan,
    )

    @app.get("/health", tags=["operations"])
    async def health() -> dict[str, str]:
        return {"status": "ready"}

    @app.get(
        "/api/companion-authority/v1/memory-runtime-roster",
        response_model=MemoryRuntimeRosterResponse,
        tags=["memory-runtime-authority"],
    )
    async def get_memory_runtime_roster(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> MemoryRuntimeRosterResponse:
        authorize_service(authorization, roster_token)
        realms: list[MemoryRuntimeRealm] = []
        for owner in await store.owners.list():
            if owner.status != "active":
                continue
            # A realm runs because its Owner is active, not because some
            # Companion is: memory belongs to the Owner, and an Owner between
            # Companions has not stopped having a memory.
            for realm in await store.memory_realms.list_for_owner(owner.owner_id):
                if realm.status != "active":
                    continue
                realms.append(
                    MemoryRuntimeRealm(
                        realm_id=realm.realm_id,
                        owner_id=realm.owner_id,
                        engine=realm.engine,
                        engine_config=dict(realm.engine_config_json or {}),
                    )
                )
        return MemoryRuntimeRosterResponse(realms=realms)

    async def _owned_companion(owner_id: str, companion_id: str):
        """This Owner's Companion, or 404.

        The only place ownership is decided in this authority. A caller that
        holds a companion_id has not thereby proved whose it is, and a Companion
        of another Owner is reported as absent rather than as forbidden so an id
        cannot be probed for existence.
        """
        row = await store.companions.get(companion_id)
        if row is None or row.owner_id != owner_id:
            raise HTTPException(status_code=404, detail="companion not found for owner")
        return row

    @app.get(
        "/api/companion-authority/v1/owners/{owner_id}/companions",
        response_model=CompanionPageResponse,
        tags=["companion-authority"],
    )
    async def list_owner_companions(
        owner_id: str,
        cursor: str | None = None,
        limit: int = ROSTER_PAGE_LIMIT,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionPageResponse:
        authorize_service(authorization, token)
        if not 1 <= limit <= ROSTER_PAGE_LIMIT:
            raise HTTPException(
                status_code=422, detail=f"limit must be between 1 and {ROSTER_PAGE_LIMIT}"
            )
        owner = await store.owners.get(owner_id)
        if owner is None:
            raise HTTPException(status_code=404, detail="owner not found")
        after = _decode_cursor(cursor) if cursor else None
        # One extra row answers "is there another page" without a second query
        # that could see a different write.
        rows = await store.companions.page_for_owner(
            owner_id, limit=limit + 1, after=after
        )
        page, has_more = rows[:limit], len(rows) > limit
        return CompanionPageResponse(
            owner_id=owner_id,
            default_companion_id=owner.default_companion_id,
            companions=[
                CompanionSummaryResponse(
                    companion_id=row.companion_id,
                    display_name=row.display_name,
                    kind=row.kind,
                    lifecycle_state=row.lifecycle_state,
                    revision=row.revision,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
                for row in page
            ],
            next_cursor=_encode_cursor(page[-1]) if has_more and page else None,
        )

    @app.get(
        "/api/companion-authority/v1/owners/{owner_id}/companions/{companion_id}",
        response_model=CompanionIdentityResponse,
        tags=["companion-authority"],
    )
    async def get_owner_companion(
        owner_id: str,
        companion_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionIdentityResponse:
        """The same row as the exact resolver, with ownership proved here.

        The resolver beside this one takes only a companion_id, because its
        caller (Kernel) is asking "may this be assigned" and does its own owner
        comparison. A product surface must not be trusted to do that comparison,
        so this route requires the Owner in the path and the authority checks it.
        """
        authorize_service(authorization, token)
        row = await _owned_companion(owner_id, companion_id)
        return CompanionIdentityResponse(
            companion_id=row.companion_id,
            owner_id=row.owner_id,
            display_name=row.display_name,
            lifecycle_state=row.lifecycle_state,
            kind=row.kind,
            revision=row.revision,
        )

    @app.get(
        "/api/companion-authority/v1/companions/{companion_id}",
        response_model=CompanionIdentityResponse,
        tags=["companion-authority"],
    )
    async def get_companion_identity(
        companion_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionIdentityResponse:
        authorize_service(authorization, token)
        row = await store.companions.get(companion_id)
        if row is None:
            raise HTTPException(status_code=404, detail="companion not found")
        return CompanionIdentityResponse(
            companion_id=row.companion_id,
            owner_id=row.owner_id,
            display_name=row.display_name,
            lifecycle_state=row.lifecycle_state,
            kind=row.kind,
            revision=row.revision,
        )

    @app.patch(
        "/api/companion-authority/v1/companions/{companion_id}",
        response_model=CompanionIdentityResponse,
        tags=["companion-authority"],
    )
    async def rename_companion(
        companion_id: str,
        payload: CompanionRenameRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionIdentityResponse:
        """The one thing about a Companion its Owner may set directly.

        This authority stays closed to everything else: it answers to Admin and
        to nobody else, and Admin is where an Owner's authority is judged. The
        surface widens by one field because a person naming their Eidolon and
        never seeing that name again is not a product working as intended.
        """

        authorize_service(authorization, token)
        display_name = payload.display_name.strip()
        if not display_name:
            raise HTTPException(status_code=422, detail="display_name cannot be blank")
        row = await store.companions.rename(companion_id, display_name)
        if row is None:
            raise HTTPException(status_code=404, detail="companion not found")
        return CompanionIdentityResponse(
            companion_id=row.companion_id,
            owner_id=row.owner_id,
            display_name=row.display_name,
            lifecycle_state=row.lifecycle_state,
            kind=row.kind,
            revision=row.revision,
        )

    @app.get(
        "/api/companion-authority/v1/companions/{companion_id}/persona-timeline",
        response_model=PersonaTimelineResponse,
        tags=["companion-authority"],
    )
    async def get_persona_timeline(
        companion_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> PersonaTimelineResponse:
        """What this Companion has been, newest first.

        Proposals are included as they are stored, because this authority
        reports what exists. Whether a person is shown them is a decision for
        the layer facing that person, and it is no.
        """

        authorize_service(authorization, token)
        companion = await store.companions.get(companion_id)
        if companion is None:
            raise HTTPException(status_code=404, detail="companion not found")
        rows = await store.persona_genomes.list_for_companion(companion_id)
        by_id = {row.genome_id: row for row in rows}
        chapters = [
            PersonaChapterResponse(
                genome_id=row.genome_id,
                version=row.version,
                # A genome's status is a proposal state (proposed / committed /
                # rejected / stale), not a Companion lifecycle. Same wire name,
                # different axis.
                lifecycle_state=row.status,
                change_summary=row.change_summary,
                restored_from_version=(
                    by_id[row.base_genome_id].version
                    if (row.source_json or {}).get("source_type") == "owner_restore"
                    and row.base_genome_id in by_id
                    else None
                ),
                is_current=row.genome_id == companion.current_genome_id,
                created_at=row.created_at.isoformat(),
            )
            for row in sorted(rows, key=lambda value: value.version, reverse=True)
        ]
        return PersonaTimelineResponse(companion_id=companion_id, chapters=chapters)

    @app.get(
        "/api/companion-authority/v1/persona-authoring-template",
        response_model=PersonaAuthoring,
        tags=["companion-authority"],
    )
    async def persona_authoring_template(
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> PersonaAuthoring:
        """Who an Eidolon is before anybody has said anything about it.

        Served by the authority that writes genomes rather than composed by a
        screen, and that is the whole point: this has to be *what would actually
        be written* if the form came back untouched. A client filling the form
        from its own constants would show a personality this Host might not use
        — and the person would have edited a description of something else.

        Owner-independent and unauthenticated beyond the service token: it is a
        product default, not anybody's data. Answering it per Owner would invite
        a per-Owner default nobody asked for — which is also why it sits on the
        persona authority rather than the Owner-scoped one, where it was first
        written. A read with no Owner in it, about what a genome starts as,
        belongs beside the genome routes.

        Round-trippable on purpose. The response is exactly the shape the
        provision request accepts, so "read it, let someone edit it, send it
        back" needs no translation step in between — and a field added to the
        genome shows up on both ends at once instead of being quietly dropped by
        whichever side forgot.
        """

        authorize_service(authorization, token)
        return PersonaAuthoring()

    @app.get(
        "/api/companion-authority/v1/companions/{companion_id}/persona",
        response_model=PersonaAuthoring,
        tags=["companion-authority"],
    )
    async def get_persona(
        companion_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> PersonaAuthoring:
        """Who this Companion is now, in the part a person wrote.

        The read an edit screen opens on. It answers with the same shape the
        write accepts, so read → change one sentence → send back needs no
        translation, and the fields a form does not show still make the trip
        instead of being wiped by their own absence.
        """

        authorize_service(authorization, token)
        current = await store.persona_genomes.get_current(companion_id)
        if current is None:
            raise HTTPException(status_code=404, detail="companion has no persona")
        return persona_authoring_of(normalize_persona_genome(current.genome_json))

    @app.put(
        "/api/companion-authority/v1/companions/{companion_id}/persona",
        response_model=PersonaChapterResponse,
        tags=["companion-authority"],
    )
    async def author_persona(
        companion_id: str,
        payload: PersonaAuthoringRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> PersonaChapterResponse:
        """Say who this Companion is now, as a new chapter rather than an edit.

        PUT because the body states an end — "this is who it is" — so a client
        that lost the answer sends the same thing again and lands in the same
        place. Sending it twice writes one chapter, because an unchanged genome
        hashes to what is already current.
        """

        authorize_service(authorization, token)
        try:
            authored = await store.persona_genomes.author(
                companion_id=companion_id,
                persona=payload.persona,
                change_summary=payload.change_summary,
            )
        except PersonaGenomeConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "code": exc.code,
                    "message": str(exc),
                    "stale_genome_id": exc.stale_genome_id,
                },
            ) from exc
        return PersonaChapterResponse(
            genome_id=authored.genome_id,
            version=authored.version,
            lifecycle_state=authored.status,
            change_summary=authored.change_summary,
            restored_from_version=None,
            is_current=True,
            created_at=authored.created_at.isoformat(),
        )

    @app.post(
        "/api/companion-authority/v1/companions/{companion_id}/persona-restorations",
        response_model=PersonaChapterResponse,
        tags=["companion-authority"],
    )
    async def restore_persona(
        companion_id: str,
        payload: PersonaRestoreRequest,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> PersonaChapterResponse:
        """Make this Companion what it was, as a new chapter rather than an undo."""

        authorize_service(authorization, token)
        try:
            restored = await store.persona_genomes.restore(
                companion_id=companion_id,
                genome_id=payload.genome_id,
                change_summary=payload.change_summary,
            )
        except PersonaGenomeConflict as exc:
            # The code travels, not only the sentence: a consumer telling "it is
            # already that" from "someone changed it first" by matching English
            # is a consumer that breaks when the wording improves.
            raise HTTPException(
                status_code=409,
                detail={
                    "code": exc.code,
                    "message": str(exc),
                    "stale_genome_id": exc.stale_genome_id,
                },
            ) from exc
        return PersonaChapterResponse(
            genome_id=restored.genome_id,
            version=restored.version,
            lifecycle_state=restored.status,
            change_summary=restored.change_summary,
            restored_from_version=(restored.source_json or {}).get("restored_version"),
            is_current=True,
            created_at=restored.created_at.isoformat(),
        )

    @app.get(
        "/api/companion-authority/v1/companions/{companion_id}/runtime-snapshot",
        response_model=CompanionRuntimeSnapshotResponse,
        tags=["companion-authority"],
    )
    async def get_companion_runtime_snapshot(
        companion_id: str,
        genome_id: str | None = None,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionRuntimeSnapshotResponse:
        authorize_service(authorization, token)
        companion = await store.companions.get(companion_id)
        if companion is None:
            raise HTTPException(status_code=404, detail="companion not found")
        if companion.lifecycle_state != "active":
            # Asking for a runtime snapshot is how a session begins, so this is
            # where "put away" has to mean something. Without it, archiving
            # changed a row and nothing else: a device that already knew the id
            # could still start talking to a Companion its owner had retired.
            # Sessions already running hold the snapshot they were given — this
            # stops new ones, which is exactly what retiring means.
            raise HTTPException(status_code=412, detail="companion is not active")
        return await _runtime_snapshot(store, companion, genome_id=genome_id)

    @app.get(
        "/api/companion-authority/v1/owners/{owner_id}/default-runtime-snapshot",
        response_model=CompanionRuntimeSnapshotResponse,
        tags=["companion-authority"],
    )
    async def get_owner_default_runtime_snapshot(
        owner_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionRuntimeSnapshotResponse:
        authorize_service(authorization, token)
        owner = await store.owners.get(owner_id)
        if owner is None:
            raise HTTPException(status_code=404, detail="owner not found")
        if owner.status != "active":
            raise HTTPException(status_code=412, detail="owner is not active")
        companion = await store.companions.get_default_for_owner(owner_id)
        if companion is None:
            # Either the pointer is unset or it points at a Companion that is no
            # longer active. Both mean the same thing to a caller asking "who
            # answers when nobody was named": nobody, and it must not guess.
            raise HTTPException(
                status_code=412, detail="owner has no active default companion"
            )
        return await _runtime_snapshot(store, companion, genome_id=None)

    @app.get(
        "/api/companion-authority/v1/companions/{companion_id}/face",
        responses={200: {"content": {"image/jpeg": {}}}, 204: {"description": "No face"}},
        tags=["companion-authority"],
    )
    async def get_companion_face(
        companion_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> Response:
        authorize_service(authorization, token)
        companion = await store.companions.get(companion_id)
        if companion is None:
            raise HTTPException(status_code=404, detail="companion not found")
        # Readable whatever state it is in, unlike the write below. Nothing can
        # start a session with a Companion that is not active — the runtime
        # snapshot refuses — so this is not a way to reach one. What it is, is
        # the only way a person is shown the face of an Eidolon they put away,
        # and a management screen that could not draw their own archived
        # Companion would be hiding it from them rather than keeping it.
        asset = await store.companion_faces.get_active(companion_id)
        if asset is None:
            return Response(status_code=204)
        try:
            data = store.object_storage.get(asset.cond_storage_key)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="companion face is unavailable") from exc
        if (
            len(data) != asset.cond_size_bytes
            or hashlib.sha256(data).hexdigest() != asset.cond_sha256
        ):
            raise HTTPException(status_code=500, detail="companion face integrity check failed")
        return Response(
            content=data,
            media_type=asset.cond_content_type,
            headers={"ETag": f'"sha256:{asset.cond_sha256}"'},
        )

    @app.put(
        "/api/companion-authority/v1/companions/{companion_id}/face",
        response_model=CompanionFaceResponse,
        tags=["companion-authority"],
    )
    async def set_companion_face(
        companion_id: str,
        request: Request,
        content_type: str | None = Header(default=None, alias="Content-Type"),
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionFaceResponse:
        """Give this Companion the face its Owner chose.

        The bytes arrive as bytes rather than wrapped in JSON. A face is a
        photograph, and base64 inside a document would inflate it by a third
        and make every layer between here and the phone hold the whole thing
        in memory twice to say the same thing.

        Setting a face supersedes the previous one rather than overwriting it:
        what an Eidolon looked like is part of what it has been.
        """

        authorize_service(authorization, token)
        if (content_type or "").split(";")[0].strip() != "image/jpeg":
            raise HTTPException(status_code=415, detail="companion face must be image/jpeg")
        companion = await store.companions.get(companion_id)
        if companion is None:
            raise HTTPException(status_code=404, detail="companion not found")
        if companion.lifecycle_state != "active":
            raise HTTPException(status_code=412, detail="companion is not active")
        data = await request.body()
        if not data:
            raise HTTPException(status_code=422, detail="companion face is empty")
        if len(data) > MAXIMUM_FACE_BYTES:
            raise HTTPException(status_code=413, detail="companion face is too large")
        if not data.startswith(JPEG_MAGIC):
            # Refused here rather than by whatever renders it later: a file that
            # is not a JPEG cannot become one, and the person choosing it is
            # still on the screen where they can choose another.
            raise HTTPException(status_code=415, detail="companion face is not a JPEG")
        digest = hashlib.sha256(data).hexdigest()
        key = f"{companion.owner_id}/companion-faces/{digest}.jpg"
        try:
            store.object_storage.put(key, data, expected_sha256=digest)
        except (OSError, ValueError) as exc:
            raise HTTPException(status_code=500, detail="companion face could not be stored") from exc
        try:
            asset = await store.companion_faces.set_face(
                companion_id=companion_id,
                cond_storage_key=key,
                cond_content_type="image/jpeg",
                cond_size_bytes=len(data),
                cond_sha256=digest,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _face_response(companion_id, asset)

    @app.delete(
        "/api/companion-authority/v1/companions/{companion_id}/face",
        response_model=CompanionFaceResponse,
        tags=["companion-authority"],
    )
    async def clear_companion_face(
        companion_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionFaceResponse:
        """Take the face away, leaving the Companion itself untouched."""

        authorize_service(authorization, token)
        companion = await store.companions.get(companion_id)
        if companion is None:
            raise HTTPException(status_code=404, detail="companion not found")
        await store.companion_faces.clear(companion_id)
        return _face_response(companion_id, None)

    @app.get(
        "/api/companion-authority/v1/companions/{companion_id}/face-state",
        response_model=CompanionFaceResponse,
        tags=["companion-authority"],
    )
    async def get_companion_face_state(
        companion_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionFaceResponse:
        """Whether there is a face, without carrying the face itself.

        A screen has to know what to show before it is worth spending a
        photograph's worth of bytes finding out.
        """

        authorize_service(authorization, token)
        companion = await store.companions.get(companion_id)
        if companion is None:
            raise HTTPException(status_code=404, detail="companion not found")
        return _face_response(
            companion_id,
            await store.companion_faces.get_active(companion_id),
        )

    return app


def _face_response(companion_id: str, asset: Any) -> CompanionFaceResponse:
    if asset is None:
        return CompanionFaceResponse(companion_id=companion_id, has_face=False)
    return CompanionFaceResponse(
        companion_id=companion_id,
        has_face=True,
        face_asset_id=asset.face_asset_id,
        sha256=asset.cond_sha256,
        size_bytes=asset.cond_size_bytes,
        updated_at=asset.created_at.isoformat(),
    )


async def _runtime_snapshot(
    store: DataStore,
    companion: Any,
    *,
    genome_id: str | None,
) -> CompanionRuntimeSnapshotResponse:
    owner = await store.owners.get(companion.owner_id)
    if owner is None:
        raise HTTPException(status_code=409, detail="companion owner is missing")
    if owner.status != "active" or companion.lifecycle_state != "active":
        raise HTTPException(status_code=412, detail="owner or companion is not active")
    if not companion.default_memory_realm_id:
        raise HTTPException(status_code=412, detail="companion has no default memory realm")
    realm = await store.memory_realms.get(companion.default_memory_realm_id)
    if realm is None:
        raise HTTPException(status_code=409, detail="default memory realm is missing")
    if realm.owner_id != companion.owner_id or realm.status != "active":
        raise HTTPException(status_code=412, detail="default memory realm is not active in scope")

    selected_genome_id = (genome_id or companion.current_genome_id or "").strip()
    if not selected_genome_id:
        raise HTTPException(status_code=412, detail="companion has no current persona genome")
    genome = await store.persona_genomes.get(selected_genome_id)
    if genome is None:
        raise HTTPException(status_code=404, detail="persona genome not found")
    if genome.companion_id != companion.companion_id or genome.status != "committed":
        raise HTTPException(status_code=412, detail="persona genome is not committed in scope")

    return CompanionRuntimeSnapshotResponse(
        owner_id=companion.owner_id,
        companion_id=companion.companion_id,
        runtime_config=dict(companion.runtime_config_json or {}),
        memory_realm=MemoryRealmSnapshot(realm_id=realm.realm_id),
        persona_genome=PersonaGenomeSnapshot(
            genome_id=genome.genome_id,
            version=genome.version,
            schema_version=genome.schema_version,
            genome_hash=genome.genome_hash,
            realizer_version=genome.realizer_version,
            genome=dict(genome.genome_json or {}),
        ),
    )
