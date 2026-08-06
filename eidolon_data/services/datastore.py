"""DataStore facade for Eidolon Data."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from eidolon_data.audit.outbox import AuditOutboxRepository
from eidolon_data.db.engine import (
    create_engine,
    create_session_factory,
    init_schema,
    validate_schema,
)
from eidolon_data.ports.memory_engine import MemoryEnginePort
from eidolon_data.repositories import (
    BodyCommandsRepository,
    CompanionFaceAssetsRepository,
    CompanionsRepository,
    DevicesRepository,
    EventsRepository,
    GuardBindingsRepository,
    GuardOwnerFaceProfileDeliveriesRepository,
    GuardPolicyActionsRepository,
    GuardRuntimeDeliveriesRepository,
    MemoryRepository,
    OwnerFaceProfilesRepository,
    OwnersRepository,
    PersonaRepository,
)
from eidolon_data.services.companion import CompanionDeletionService
from eidolon_data.services.maintenance import MaintenanceService
from eidolon_data.services.memory_service import MemoryService
from eidolon_data.services.object_storage import LocalObjectStorage
from eidolon_data.services.owner_workspace import CompanionWorkspaceService, OwnerService
from eidolon_data.services.persona_service import PersonaService
from eidolon_data.settings import DataSettings


@dataclass
class DataStore:
    """System-data authority facade hosted by the Admin control plane.

    Device/event accessors are transitional removal targets; Agent runtime has
    already moved to its own authority store.
    """

    settings: DataSettings
    engine: AsyncEngine
    session_factory: async_sessionmaker
    memory_engine: MemoryEnginePort | None = None

    @classmethod
    def open(
        cls,
        settings: DataSettings | None = None,
        *,
        memory_engine: MemoryEnginePort | None = None,
    ) -> DataStore:
        resolved = settings or DataSettings()
        engine = create_engine(resolved)
        session_factory = create_session_factory(engine)
        return cls(
            settings=resolved,
            engine=engine,
            session_factory=session_factory,
            memory_engine=memory_engine,
        )

    async def init_schema(self) -> None:
        await init_schema(self.engine)

    async def validate_schema(self) -> None:
        """Validate an Alembic-managed production schema without mutating it."""
        await validate_schema(self.engine)

    async def close(self) -> None:
        await self.engine.dispose()

    @property
    def owners(self) -> OwnersRepository:
        return OwnersRepository(self.session_factory)

    @property
    def companions(self) -> CompanionsRepository:
        return CompanionsRepository(self.session_factory)

    @property
    def persona_repo(self) -> PersonaRepository:
        return PersonaRepository(self.session_factory)

    @property
    def companion_face_assets(self) -> CompanionFaceAssetsRepository:
        return CompanionFaceAssetsRepository(self.session_factory)

    @property
    def devices(self) -> DevicesRepository:
        return DevicesRepository(self.session_factory)

    @property
    def guard_bindings(self) -> GuardBindingsRepository:
        return GuardBindingsRepository(self.session_factory)

    @property
    def guard_actions(self) -> GuardPolicyActionsRepository:
        return GuardPolicyActionsRepository(self.session_factory)

    @property
    def guard_runtime_deliveries(self) -> GuardRuntimeDeliveriesRepository:
        return GuardRuntimeDeliveriesRepository(self.session_factory)

    @property
    def owner_face_profiles(self) -> OwnerFaceProfilesRepository:
        return OwnerFaceProfilesRepository(self.session_factory)

    @property
    def guard_owner_face_profile_deliveries(
        self,
    ) -> GuardOwnerFaceProfileDeliveriesRepository:
        return GuardOwnerFaceProfileDeliveriesRepository(self.session_factory)

    @property
    def object_storage(self) -> LocalObjectStorage:
        return LocalObjectStorage(self.settings.object_store_path)

    @property
    def body_commands(self) -> BodyCommandsRepository:
        return BodyCommandsRepository(self.session_factory)

    @property
    def memory_repo(self) -> MemoryRepository:
        return MemoryRepository(self.session_factory)

    @property
    def events(self) -> EventsRepository:
        return EventsRepository(self.session_factory)

    @property
    def audit_outbox(self) -> AuditOutboxRepository:
        """Durable producer-local hand-off to the independent audit plane."""

        return AuditOutboxRepository(self.session_factory)

    @property
    def persona(self) -> PersonaService:
        return PersonaService(self.session_factory)

    @property
    def memory(self) -> MemoryService:
        return MemoryService(repository=self.memory_repo, engine=self.memory_engine)

    @property
    def owner_service(self) -> OwnerService:
        return OwnerService(self.session_factory)

    @property
    def workspace_provisioning(self) -> CompanionWorkspaceService:
        return CompanionWorkspaceService(self.session_factory)

    @property
    def dev_maintenance(self) -> MaintenanceService:
        return MaintenanceService(self.session_factory)

    @property
    def companion_deletion(self) -> CompanionDeletionService:
        return CompanionDeletionService(self.session_factory)

    @property
    def companion_workspace(self) -> CompanionWorkspaceService:
        """Compatibility alias for workspace provisioning operations."""

        return self.workspace_provisioning

    @property
    def maintenance(self) -> MaintenanceService:
        """Compatibility alias for local-development maintenance operations."""

        return self.dev_maintenance
