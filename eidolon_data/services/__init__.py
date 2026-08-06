"""Domain services and facades."""

from eidolon_data.services.companion import (
    CompanionDeletionError,
    CompanionDeletionResult,
    CompanionDeletionService,
)
from eidolon_data.services.datastore import DataStore
from eidolon_data.services.owner_deletion import OwnerDeletionResult, OwnerDeletionService
from eidolon_data.services.owner_workspace import (
    CompanionWorkspaceResult,
    CompanionWorkspaceService,
    OwnerCreateResult,
    OwnerService,
    OwnerWorkspaceError,
)
from eidolon_data.services.persona_service import PersonaService

__all__ = [
    "CompanionDeletionError",
    "CompanionDeletionResult",
    "CompanionDeletionService",
    "CompanionWorkspaceResult",
    "CompanionWorkspaceService",
    "DataStore",
    "OwnerCreateResult",
    "OwnerDeletionResult",
    "OwnerDeletionService",
    "OwnerService",
    "OwnerWorkspaceError",
    "PersonaService",
]
