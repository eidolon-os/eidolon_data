"""Repository implementations for the Eidolon Data schema."""

from eidolon_data.repositories.companions import CompanionsRepository
from eidolon_data.repositories.conversations import ConversationsRepository
from eidolon_data.repositories.devices import DevicesRepository
from eidolon_data.repositories.events import EventsRepository
from eidolon_data.repositories.jobs import JobsRepository
from eidolon_data.repositories.memory import MemoryRepository
from eidolon_data.repositories.owners import OwnersRepository
from eidolon_data.repositories.persona import PersonaRepository

__all__ = [
    "CompanionsRepository",
    "ConversationsRepository",
    "DevicesRepository",
    "EventsRepository",
    "JobsRepository",
    "MemoryRepository",
    "OwnersRepository",
    "PersonaRepository",
]
