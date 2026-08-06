"""System-data half of companion teardown.

Agent runtime is a separate authority and must be deleted through the Agent
admin port before this transaction runs. This service owns only System Data.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from eidolon_data.schema.models import (
    BodyCommandRow,
    CompanionFaceAssetRow,
    CompanionRow,
    DeviceRow,
    MemoryRealmRow,
    PersonaGenomeRow,
)


class CompanionDeletionError(ValueError):
    """Raised when a companion delete violates a domain rule."""


@dataclass(frozen=True)
class CompanionDeletionResult:
    owner_id: str
    companion_id: str
    deleted: bool
    # Memory realms whose DB rows were removed. The caller (admin) is
    # responsible for purging the corresponding memory palaces, since realm_id
    # == memory_space_id and the palace lives outside this database.
    realm_ids: list[str]
    device_ids: list[str]
    # Object-store keys for the companion's display-face assets. The caller
    # (admin) purges the corresponding blobs, since the bytes live outside this
    # database — same contract as ``realm_ids`` for memory palaces.
    face_asset_storage_keys: list[str]
    counts: dict[str, int]


class CompanionDeletionService:
    """Hard-delete a single companion and all of its owned rows."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def delete_companion(
        self,
        *,
        owner_id: str,
        companion_id: str,
        allow_master: bool = False,
    ) -> CompanionDeletionResult:
        async with self._session_factory() as session, session.begin():
            companion = await session.get(CompanionRow, companion_id)
            if companion is None or companion.owner_id != owner_id:
                raise CompanionDeletionError("companion not found for owner")
            if companion.is_master and not allow_master:
                raise CompanionDeletionError(
                    "refusing to delete master companion (pass allow_master to override)"
                )

            realm_ids = list(
                await session.scalars(
                    select(MemoryRealmRow.realm_id).where(
                        MemoryRealmRow.companion_id == companion_id
                    )
                )
            )
            device_ids = list(
                await session.scalars(
                    select(DeviceRow.device_id)
                    .where(DeviceRow.bound_companion_id == companion_id)
                    .where(DeviceRow.owner_id == owner_id)
                )
            )
            face_asset_rows = (
                await session.execute(
                    select(
                        CompanionFaceAssetRow.cond_storage_key,
                        CompanionFaceAssetRow.idle_storage_key,
                    ).where(CompanionFaceAssetRow.companion_id == companion_id)
                )
            ).all()
            face_asset_storage_keys: list[str] = []
            for cond_key, idle_key in face_asset_rows:
                face_asset_storage_keys.append(cond_key)
                if idle_key:
                    face_asset_storage_keys.append(idle_key)
            counts: dict[str, int] = {}

            async def _del(stmt, key: str) -> None:
                result = await session.execute(stmt)
                counts[key] = int(result.rowcount or 0)

            await _del(
                delete(BodyCommandRow).where(BodyCommandRow.companion_id == companion_id),
                "body_commands",
            )
            if device_ids:
                await _del(delete(DeviceRow).where(DeviceRow.device_id.in_(device_ids)), "devices")
            if realm_ids:
                await _del(
                    delete(MemoryRealmRow).where(MemoryRealmRow.realm_id.in_(realm_ids)),
                    "memory_realms",
                )
            await _del(
                delete(CompanionFaceAssetRow).where(
                    CompanionFaceAssetRow.companion_id == companion_id
                ),
                "companion_face_assets",
            )
            await _del(
                delete(PersonaGenomeRow).where(PersonaGenomeRow.companion_id == companion_id),
                "persona_genomes",
            )
            await _del(
                delete(CompanionRow).where(CompanionRow.companion_id == companion_id),
                "companions",
            )

        return CompanionDeletionResult(
            owner_id=owner_id,
            companion_id=companion_id,
            deleted=True,
            realm_ids=realm_ids,
            device_ids=device_ids,
            face_asset_storage_keys=face_asset_storage_keys,
            counts=counts,
        )
