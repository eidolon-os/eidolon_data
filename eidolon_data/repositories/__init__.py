"""Persistence queries and aggregate stores for the final System Data schema."""

from eidolon_data.repositories.companion_face_assets import CompanionFaceAssetsRepository
from eidolon_data.repositories.companions import CompanionsRepository
from eidolon_data.repositories.guard_bindings import GuardBindingsRepository
from eidolon_data.repositories.memory import MemoryRealmsRepository
from eidolon_data.repositories.owner_face_profiles import OwnerFaceProfilesRepository
from eidolon_data.repositories.owners import OwnersRepository
from eidolon_data.repositories.persona import PersonaRepository

__all__ = [
    "CompanionFaceAssetsRepository",
    "CompanionsRepository",
    "GuardBindingsRepository",
    "MemoryRealmsRepository",
    "OwnerFaceProfilesRepository",
    "OwnersRepository",
    "PersonaRepository",
]
