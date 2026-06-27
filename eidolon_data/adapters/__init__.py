"""Compatibility adapters for sibling projects during migration."""

from eidolon_data.adapters.admin_registry import (
    EidolonDataAgentMetadataRepository,
    EidolonDataDeviceBindingRepository,
    EidolonDataTenantRepository,
    EidolonDataUserRepository,
)
from eidolon_data.adapters.hub_registry import EidolonDataDeviceRegistryRepository
from eidolon_data.adapters.runtime_resolve import EidolonDataResolveClient

__all__ = [
    "EidolonDataAgentMetadataRepository",
    "EidolonDataDeviceBindingRepository",
    "EidolonDataDeviceRegistryRepository",
    "EidolonDataResolveClient",
    "EidolonDataTenantRepository",
    "EidolonDataUserRepository",
]
