"""Versioned, least-privilege Companion identity authority service."""

from __future__ import annotations

import hashlib
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Response
from pydantic import BaseModel, ConfigDict, Field

from eidolon_data import DataSettings, DataStore, load_settings
from eidolon_data.repositories.persona import PersonaGenomeConflict

from .service_auth import authorize_service, required_service_token


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
    lifecycle_state: Literal["active", "inactive"]


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


class MemoryRuntimeRealm(BaseModel):
    """One active Memory Realm and the authority facts needed to run it."""

    model_config = ConfigDict(extra="forbid")

    realm_id: str = Field(min_length=1, max_length=64)
    owner_id: str = Field(min_length=1, max_length=64)
    companion_id: str = Field(min_length=1, max_length=64)
    engine: str = Field(min_length=1, max_length=64)
    engine_config: dict[str, Any]


class MemoryRuntimeRosterResponse(BaseModel):
    """Active Memory runtime roster projected by the System Data authority."""

    model_config = ConfigDict(extra="forbid")

    contract_version: Literal["1"] = "1"
    operation: Literal["memory.runtime-roster"] = "memory.runtime-roster"
    realms: list[MemoryRuntimeRealm]


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
            companions = {
                companion.companion_id: companion
                for companion in await store.companions.list_for_owner(owner.owner_id)
                if companion.status == "active"
            }
            for realm in await store.memory_realms.list_for_owner(owner.owner_id):
                if realm.status != "active" or realm.companion_id not in companions:
                    continue
                realms.append(
                    MemoryRuntimeRealm(
                        realm_id=realm.realm_id,
                        owner_id=realm.owner_id,
                        companion_id=realm.companion_id,
                        engine=realm.engine,
                        engine_config=dict(realm.engine_config_json or {}),
                    )
                )
        return MemoryRuntimeRosterResponse(realms=realms)

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
            lifecycle_state="active" if row.status == "active" else "inactive",
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
            lifecycle_state="active" if row.status == "active" else "inactive",
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
            raise HTTPException(status_code=409, detail=str(exc)) from exc
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
        return await _runtime_snapshot(store, companion, genome_id=genome_id)

    @app.get(
        "/api/companion-authority/v1/owners/{owner_id}/primary-runtime-snapshot",
        response_model=CompanionRuntimeSnapshotResponse,
        tags=["companion-authority"],
    )
    async def get_owner_primary_runtime_snapshot(
        owner_id: str,
        authorization: str | None = Header(default=None, alias="Authorization"),
    ) -> CompanionRuntimeSnapshotResponse:
        authorize_service(authorization, token)
        owner = await store.owners.get(owner_id)
        if owner is None:
            raise HTTPException(status_code=404, detail="owner not found")
        if owner.status != "active":
            raise HTTPException(status_code=412, detail="owner is not active")
        companion = await store.companions.get_primary_for_owner(owner_id)
        if companion is None:
            raise HTTPException(status_code=412, detail="owner has no active primary companion")
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
        if companion.status != "active":
            raise HTTPException(status_code=412, detail="companion is not active")
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

    return app


async def _runtime_snapshot(
    store: DataStore,
    companion: Any,
    *,
    genome_id: str | None,
) -> CompanionRuntimeSnapshotResponse:
    owner = await store.owners.get(companion.owner_id)
    if owner is None:
        raise HTTPException(status_code=409, detail="companion owner is missing")
    if owner.status != "active" or companion.status != "active":
        raise HTTPException(status_code=412, detail="owner or companion is not active")
    if not companion.default_memory_realm_id:
        raise HTTPException(status_code=412, detail="companion has no default memory realm")
    realm = await store.memory_realms.get(companion.default_memory_realm_id)
    if realm is None:
        raise HTTPException(status_code=409, detail="default memory realm is missing")
    if (
        realm.owner_id != companion.owner_id
        or realm.companion_id != companion.companion_id
        or realm.status != "active"
    ):
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
