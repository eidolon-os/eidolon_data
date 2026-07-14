"""Allow multiple Guard companions and label historical Guard identities."""

from __future__ import annotations

from hashlib import sha256

import sqlalchemy as sa
from alembic import op
from eidolon_sdk.biz.persona import (
    PERSONA_GENOME_SCHEMA,
    PERSONA_REALIZER,
    build_default_persona_genome,
    persona_genome_hash,
    persona_genome_to_json,
)
from sqlalchemy.orm import Session

from eidolon_data.schema.models import CompanionRow, MemoryRealmRow, PersonaGenomeRow

revision = "0016_multi_guard_companion_workspace"
down_revision = "0015_guard_binding_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # P0 used owner_id as the active-binding uniqueness key, which made a second
    # physical Guard impossible even when it had its own companion.  A Guard
    # companion is now the unit of replacement; one device cannot be active in
    # more than one binding and one companion cannot control two devices.
    op.drop_index("uq_guard_bindings_owner_active", table_name="guard_bindings")
    op.create_index(
        "uq_guard_bindings_guard_companion_active",
        "guard_bindings",
        ["guard_companion_id"],
        unique=True,
        sqlite_where=sa.text("state = 'active'"),
        postgresql_where=sa.text("state = 'active'"),
    )
    op.execute(
        "UPDATE companions "
        "SET companion_type = 'guard' "
        "WHERE kind = 'guard' AND companion_type = 'slave'"
    )
    _backfill_guard_workspaces()


def downgrade() -> None:
    op.drop_index("uq_guard_bindings_guard_companion_active", table_name="guard_bindings")
    op.create_index(
        "uq_guard_bindings_owner_active",
        "guard_bindings",
        ["owner_id"],
        unique=True,
        sqlite_where=sa.text("state = 'active'"),
        postgresql_where=sa.text("state = 'active'"),
    )
    op.execute(
        "UPDATE companions "
        "SET companion_type = 'slave' "
        "WHERE kind = 'guard' AND companion_type = 'guard'"
    )


def _backfill_guard_workspaces() -> None:
    """Bring pre-workspace Guard identities up to the normal resolve contract."""
    with Session(bind=op.get_bind()) as session:
        companions = list(
            session.scalars(
                sa.select(CompanionRow).where(CompanionRow.kind == "guard")
            )
        )
        for companion in companions:
            companion.companion_type = "guard"
            companion.metadata_json = {
                **(companion.metadata_json or {}),
                "guard_control_plane": True,
                "runtime_role": "guard",
                "workspace_mode": "compatibility",
            }
            genome_id, realm_id = _workspace_ids(companion.companion_id)
            genome = (
                session.get(PersonaGenomeRow, companion.current_genome_id)
                if companion.current_genome_id
                else None
            )
            if genome is None:
                genome = session.scalar(
                    sa.select(PersonaGenomeRow)
                    .where(
                        PersonaGenomeRow.companion_id == companion.companion_id,
                        PersonaGenomeRow.status == "committed",
                    )
                    .order_by(PersonaGenomeRow.version.desc())
                )
            if genome is None:
                genome = PersonaGenomeRow(
                    genome_id=genome_id,
                    companion_id=companion.companion_id,
                    version=1,
                    status="committed",
                    schema_version=PERSONA_GENOME_SCHEMA,
                    genome_hash="",  # filled immediately below from the canonical payload
                    realizer_version=PERSONA_REALIZER,
                    source_json={"source_type": "guard_workspace_migration"},
                    genome_json={},
                    change_summary="Guard compatibility workspace genome",
                )
                payload = persona_genome_to_json(
                    build_default_persona_genome(
                        name=companion.display_name or companion.companion_id,
                        archetype="companion",
                        origin="guard_workspace_migration",
                    )
                )
                payload["provenance"] = {
                    **dict(payload.get("provenance") or {}),
                    "owner_id": companion.owner_id,
                    "companion_id": companion.companion_id,
                    "runtime_role": "guard",
                }
                genome.genome_json = payload
                genome.genome_hash = persona_genome_hash(payload)
                session.add(genome)
            companion.current_genome_id = genome.genome_id

            realm = (
                session.get(MemoryRealmRow, companion.default_memory_realm_id)
                if companion.default_memory_realm_id
                else None
            )
            if realm is None or realm.status != "active":
                realm = session.scalar(
                    sa.select(MemoryRealmRow)
                    .where(
                        MemoryRealmRow.owner_id == companion.owner_id,
                        MemoryRealmRow.companion_id == companion.companion_id,
                        MemoryRealmRow.status == "active",
                    )
                    .order_by(MemoryRealmRow.created_at.desc())
                )
            if realm is None:
                realm = MemoryRealmRow(
                    realm_id=realm_id,
                    owner_id=companion.owner_id,
                    companion_id=companion.companion_id,
                    engine="mempalace",
                    policy_json={"scope": "guard", "recall": "guard_isolated"},
                    status="active",
                )
                session.add(realm)
            companion.default_memory_realm_id = realm.realm_id
        session.flush()


def _workspace_ids(companion_id: str) -> tuple[str, str]:
    digest = sha256(companion_id.encode("utf-8")).hexdigest()[:40]
    return f"g_guard_{digest}", f"r_guard_{digest}"
