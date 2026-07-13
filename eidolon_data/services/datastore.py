"""DataStore facade for Eidolon Data."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from eidolon_data.db.engine import create_engine, create_session_factory, init_schema
from eidolon_data.ports.memory_engine import MemoryEnginePort
from eidolon_data.repositories import (
    BodyCommandsRepository,
    CompanionsRepository,
    ConversationsRepository,
    DevicesRepository,
    EventsRepository,
    GuardBindingsRepository,
    GuardPolicyActionsRepository,
    GuardRuntimeDeliveriesRepository,
    JobsRepository,
    MemoryRepository,
    OwnersRepository,
    PersonaRepository,
    RuntimeCallersRepository,
    RuntimeSessionsRepository,
)
from eidolon_data.services.companion import CompanionDeletionService
from eidolon_data.services.maintenance import MaintenanceService
from eidolon_data.services.memory_service import MemoryService
from eidolon_data.services.owner_data import OwnerDataService
from eidolon_data.services.owner_workspace import CompanionWorkspaceService, OwnerService
from eidolon_data.services.persona_service import PersonaService
from eidolon_data.settings import DataSettings


@dataclass
class DataStore:
    """Convenience facade used by agent/admin/hub/channel hot paths."""

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
    def body_commands(self) -> BodyCommandsRepository:
        return BodyCommandsRepository(self.session_factory)

    @property
    def runtime_callers(self) -> RuntimeCallersRepository:
        return RuntimeCallersRepository(self.session_factory)

    @property
    def runtime_sessions(self) -> RuntimeSessionsRepository:
        return RuntimeSessionsRepository(self.session_factory)

    @property
    def conversations(self) -> ConversationsRepository:
        return ConversationsRepository(self.session_factory)

    @property
    def memory_repo(self) -> MemoryRepository:
        return MemoryRepository(self.session_factory)

    @property
    def jobs(self) -> JobsRepository:
        return JobsRepository(self.session_factory)

    @property
    def events(self) -> EventsRepository:
        return EventsRepository(self.session_factory)

    @property
    def persona(self) -> PersonaService:
        return PersonaService(self.session_factory)

    @property
    def memory(self) -> MemoryService:
        return MemoryService(repository=self.memory_repo, engine=self.memory_engine)

    @property
    def owner_data_ops(self) -> OwnerDataService:
        return OwnerDataService(self.session_factory)

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
    def owner_data(self) -> OwnerDataService:
        """Compatibility alias for owner-scoped data governance operations."""

        return self.owner_data_ops

    @property
    def companion_workspace(self) -> CompanionWorkspaceService:
        """Compatibility alias for workspace provisioning operations."""

        return self.workspace_provisioning

    @property
    def maintenance(self) -> MaintenanceService:
        """Compatibility alias for local-development maintenance operations."""

        return self.dev_maintenance
