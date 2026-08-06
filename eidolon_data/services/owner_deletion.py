"""Explicit destructive command for deleting an Owner aggregate."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from eidolon_data.audit import governance_fact
from eidolon_data.schema import (
    CompanionFaceAssetRow,
    CompanionRow,
    GuardBindingRow,
    MemoryRealmRow,
    OwnerFaceProfileRevisionRow,
    OwnerFaceReferenceRow,
    OwnerRow,
    PersonaGenomeRow,
)


@dataclass(frozen=True)
class OwnerDeletionResult:
    owner_id: str
    deleted: bool
    realm_ids: tuple[str, ...]
    object_storage_keys: tuple[str, ...]
    deleted_rows: dict[str, int]


class OwnerDeletionService:
    """Hard-delete the System Data tree and report external cleanup keys."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def delete_owner(self, owner_id: str) -> OwnerDeletionResult:
        async with self._session_factory() as session, session.begin():
            owner = await session.get(OwnerRow, owner_id)
            if owner is None:
                return OwnerDeletionResult(
                    owner_id=owner_id,
                    deleted=False,
                    realm_ids=(),
                    object_storage_keys=(),
                    deleted_rows={},
                )
            companion_ids = tuple(
                await session.scalars(
                    select(CompanionRow.companion_id).where(CompanionRow.owner_id == owner_id)
                )
            )
            realm_ids = tuple(
                await session.scalars(
                    select(MemoryRealmRow.realm_id).where(MemoryRealmRow.owner_id == owner_id)
                )
            )
            face_rows = (
                await session.execute(
                    select(
                        CompanionFaceAssetRow.cond_storage_key,
                        CompanionFaceAssetRow.idle_storage_key,
                    ).where(CompanionFaceAssetRow.owner_id == owner_id)
                )
            ).all()
            owner_face_keys = tuple(
                await session.scalars(
                    select(OwnerFaceReferenceRow.storage_key)
                    .join(
                        OwnerFaceProfileRevisionRow,
                        OwnerFaceProfileRevisionRow.profile_revision_id
                        == OwnerFaceReferenceRow.profile_revision_id,
                    )
                    .where(OwnerFaceProfileRevisionRow.owner_id == owner_id)
                )
            )
            object_keys = (
                tuple(
                    key for cond_key, idle_key in face_rows for key in (cond_key, idle_key) if key
                )
                + owner_face_keys
            )
            deleted_rows = {
                "owners": 1,
                "companions": len(companion_ids),
                "persona_genomes": await _count_persona_genomes(session, companion_ids),
                "memory_realms": len(realm_ids),
                "companion_face_assets": len(face_rows),
                "guard_bindings": int(
                    await session.scalar(
                        select(func.count(GuardBindingRow.binding_id)).where(
                            GuardBindingRow.owner_id == owner_id
                        )
                    )
                    or 0
                ),
                "owner_face_profile_revisions": int(
                    await session.scalar(
                        select(func.count(OwnerFaceProfileRevisionRow.profile_revision_id)).where(
                            OwnerFaceProfileRevisionRow.owner_id == owner_id
                        )
                    )
                    or 0
                ),
                "owner_face_references": len(owner_face_keys),
            }
            # Companion pointers target rows that also cascade from Owner.
            if companion_ids:
                companions = await session.scalars(
                    select(CompanionRow).where(CompanionRow.companion_id.in_(companion_ids))
                )
                for companion in companions:
                    companion.current_genome_id = None
                    companion.default_memory_realm_id = None
                await session.flush()
            await session.execute(delete(OwnerRow).where(OwnerRow.owner_id == owner_id))
            session.add(
                governance_fact(
                    owner_id=owner_id,
                    subject_type="owner",
                    subject_id=owner_id,
                    action="owner.deleted",
                    payload={
                        "realm_ids": list(realm_ids),
                        "deleted_rows": deleted_rows,
                    },
                )
            )
        return OwnerDeletionResult(
            owner_id=owner_id,
            deleted=True,
            realm_ids=realm_ids,
            object_storage_keys=object_keys,
            deleted_rows=deleted_rows,
        )


async def _count_persona_genomes(session, companion_ids: tuple[str, ...]) -> int:
    if not companion_ids:
        return 0
    return int(
        await session.scalar(
            select(func.count(PersonaGenomeRow.genome_id)).where(
                PersonaGenomeRow.companion_id.in_(companion_ids)
            )
        )
        or 0
    )
