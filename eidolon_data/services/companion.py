"""System Data transaction for Companion aggregate deletion."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from eidolon_data.audit import governance_fact
from eidolon_data.schema import (
    CompanionFaceAssetRow,
    CompanionRow,
    GuardBindingRow,
    OwnerRow,
    PersonaGenomeRow,
)


class CompanionDeletionError(ValueError):
    """Raised when Companion deletion violates an aggregate invariant."""


@dataclass(frozen=True)
class CompanionDeletionResult:
    owner_id: str
    companion_id: str
    face_asset_storage_keys: tuple[str, ...]
    deleted_rows: dict[str, int]


class CompanionDeletionService:
    """Delete only System Data rows and return external cleanup references."""

    def __init__(self, session_factory: async_sessionmaker) -> None:
        self._session_factory = session_factory

    async def delete_companion(
        self,
        *,
        owner_id: str,
        companion_id: str,
        allow_default: bool = False,
    ) -> CompanionDeletionResult:
        async with self._session_factory() as session, session.begin():
            companion = await session.get(CompanionRow, companion_id)
            if companion is None or companion.owner_id != owner_id:
                raise CompanionDeletionError("companion not found for owner")
            owner = await session.get(OwnerRow, owner_id)
            is_default = owner is not None and owner.default_companion_id == companion_id
            if is_default and not allow_default:
                raise CompanionDeletionError(
                    "refusing to delete this Owner's default companion without allow_default"
                )

            face_rows = (
                await session.execute(
                    select(
                        CompanionFaceAssetRow.cond_storage_key,
                        CompanionFaceAssetRow.idle_storage_key,
                    ).where(CompanionFaceAssetRow.companion_id == companion_id)
                )
            ).all()
            storage_keys = tuple(
                key for cond_key, idle_key in face_rows for key in (cond_key, idle_key) if key
            )
            deleted_rows = {
                "companions": 1,
                "persona_genomes": await _count(
                    session,
                    PersonaGenomeRow.genome_id,
                    PersonaGenomeRow.companion_id == companion_id,
                ),
                # Deliberately absent: memory_realms. The realm belongs to the
                # Owner and outlives any one Companion, so deleting a Companion
                # deletes no memory. Removing this Companion's own statements
                # from the Owner's memory is a separate, audited act with its
                # own surface (forget), not a side effect of deletion.
                "companion_face_assets": len(face_rows),
                "guard_bindings": await _count(
                    session,
                    GuardBindingRow.binding_id,
                    GuardBindingRow.guard_companion_id == companion_id,
                ),
            }

            # Break the pointers before deleting their targets. The Owner's
            # default is one of them: SET NULL would clear the column anyway,
            # but doing it here keeps the revision bump and the audit trail with
            # the act rather than leaving a silent side effect in the schema.
            if is_default and owner is not None:
                owner.default_companion_id = None
                owner.revision += 1
            companion.current_genome_id = None
            companion.default_memory_realm_id = None
            await session.flush()
            await session.execute(
                delete(CompanionRow).where(CompanionRow.companion_id == companion_id)
            )
            session.add(
                governance_fact(
                    owner_id=owner_id,
                    subject_type="companion",
                    subject_id=companion_id,
                    action="companion.deleted",
                    payload={"deleted_rows": deleted_rows},
                )
            )

        return CompanionDeletionResult(
            owner_id=owner_id,
            companion_id=companion_id,
            face_asset_storage_keys=storage_keys,
            deleted_rows=deleted_rows,
        )


async def _count(session, column, condition) -> int:
    return int(await session.scalar(select(func.count(column)).where(condition)) or 0)
