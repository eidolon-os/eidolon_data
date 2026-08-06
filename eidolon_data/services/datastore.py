"""Composition root for the System Data authority."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from eidolon_data.audit.outbox import AuditOutboxRepository
from eidolon_data.db.engine import (
    create_engine,
    create_session_factory,
    init_schema,
    validate_schema,
)
from eidolon_data.repositories import (
    CompanionFaceAssetsRepository,
    CompanionsRepository,
    GuardBindingsRepository,
    MemoryRealmsRepository,
    OwnerFaceProfilesRepository,
    OwnersRepository,
    PersonaRepository,
)
from eidolon_data.services.companion import CompanionDeletionService
from eidolon_data.services.object_storage import LocalObjectStorage
from eidolon_data.services.owner_deletion import OwnerDeletionService
from eidolon_data.services.owner_workspace import CompanionWorkspaceService, OwnerService
from eidolon_data.services.persona_service import PersonaService
from eidolon_data.settings import DataSettings


class DataStore:
    """Explicit access point to System Data reads, commands, and infrastructure.

    The facade intentionally exposes no Device, runtime, memory-operation, or
    generic-event API. Callers should use the narrow versioned authority HTTP
    contract instead of importing this composition root across projects.
    """

    def __init__(
        self,
        *,
        settings: DataSettings,
        engine: AsyncEngine,
        session_factory: async_sessionmaker,
    ) -> None:
        self.settings = settings
        self._engine = engine
        self._session_factory = session_factory

    @classmethod
    def open(cls, settings: DataSettings | None = None) -> DataStore:
        resolved = settings or DataSettings()
        engine = create_engine(resolved)
        return cls(
            settings=resolved,
            engine=engine,
            session_factory=create_session_factory(engine),
        )

    async def init_schema(self) -> None:
        """Create an isolated development/test schema; production uses Alembic."""

        if self.settings.sqlite_read_only:
            raise RuntimeError("read-only System Data clients cannot initialize schema")
        await init_schema(self._engine)

    async def validate_schema(self) -> None:
        await validate_schema(self._engine)
        if self.settings.sqlite_read_only:
            from sqlalchemy import text

            async with self._engine.connect() as connection:
                query_only = int((await connection.execute(text("PRAGMA query_only"))).scalar_one())
                if query_only != 1:
                    raise RuntimeError("System Data reader is not query-only")

    async def close(self) -> None:
        await self._engine.dispose()

    @property
    def owners(self) -> OwnersRepository:
        return OwnersRepository(self._session_factory)

    @property
    def companions(self) -> CompanionsRepository:
        return CompanionsRepository(self._session_factory)

    @property
    def persona_genomes(self) -> PersonaRepository:
        return PersonaRepository(self._session_factory)

    @property
    def memory_realms(self) -> MemoryRealmsRepository:
        return MemoryRealmsRepository(self._session_factory)

    @property
    def guard_bindings(self) -> GuardBindingsRepository:
        return GuardBindingsRepository(self._session_factory)

    @property
    def companion_faces(self) -> CompanionFaceAssetsRepository:
        return CompanionFaceAssetsRepository(self._session_factory)

    @property
    def owner_faces(self) -> OwnerFaceProfilesRepository:
        return OwnerFaceProfilesRepository(self._session_factory)

    @property
    def audit_outbox(self) -> AuditOutboxRepository:
        return AuditOutboxRepository(self._session_factory)

    @property
    def object_storage(self) -> LocalObjectStorage:
        return LocalObjectStorage(self.settings.object_store_path)

    @property
    def owner_commands(self) -> OwnerService:
        return OwnerService(self._session_factory)

    @property
    def companion_workspaces(self) -> CompanionWorkspaceService:
        return CompanionWorkspaceService(self._session_factory)

    @property
    def persona_commands(self) -> PersonaService:
        return PersonaService(self._session_factory)

    @property
    def companion_deletion(self) -> CompanionDeletionService:
        return CompanionDeletionService(self._session_factory)

    @property
    def owner_deletion(self) -> OwnerDeletionService:
        return OwnerDeletionService(self._session_factory)
