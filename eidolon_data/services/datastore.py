"""DataStore facade for Eidolon Data."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from eidolon_data.db.engine import create_engine, create_session_factory, init_schema
from eidolon_data.ports.memory_engine import MemoryEnginePort
from eidolon_data.repositories import (
    CompanionsRepository,
    ConversationsRepository,
    DevicesRepository,
    EventsRepository,
    JobsRepository,
    MemoryRepository,
    OwnersRepository,
    PersonaRepository,
)
from eidolon_data.services.memory_service import MemoryService
from eidolon_data.services.owner_workspace import CompanionWorkspaceService, OwnerService
from eidolon_data.services.persona_service import PersonaService
from eidolon_data.services.user_data import UserDataService
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
        return PersonaService(
            persona_repo=self.persona_repo,
            companions=self.companions,
            events=self.events,
        )

    @property
    def memory(self) -> MemoryService:
        return MemoryService(repository=self.memory_repo, engine=self.memory_engine)

    @property
    def user_data(self) -> UserDataService:
        return UserDataService(self.session_factory)

    @property
    def owner_service(self) -> OwnerService:
        return OwnerService(self.session_factory)

    @property
    def companion_workspace(self) -> CompanionWorkspaceService:
        return CompanionWorkspaceService(self.session_factory)
