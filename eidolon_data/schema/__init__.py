"""Internal SQLAlchemy mapping registry.

Alembic imports this module so every final V2 table is registered on the shared
metadata. Product integrations consume application/API contracts, not rows.
"""

from eidolon_data.schema.assets import (
    CompanionFaceAssetRow,
    OwnerFaceProfileRevisionRow,
    OwnerFaceReferenceRow,
)
from eidolon_data.schema.audit import AuditOutboxRow
from eidolon_data.schema.core import (
    CompanionRow,
    MemoryRealmRow,
    OwnerRow,
    PersonaGenomeRow,
)
from eidolon_data.schema.guard import GuardBindingRow

__all__ = [
    "AuditOutboxRow",
    "CompanionFaceAssetRow",
    "CompanionRow",
    "GuardBindingRow",
    "MemoryRealmRow",
    "OwnerFaceProfileRevisionRow",
    "OwnerFaceReferenceRow",
    "OwnerRow",
    "PersonaGenomeRow",
]
