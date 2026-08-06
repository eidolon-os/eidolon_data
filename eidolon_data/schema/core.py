"""Persistence rows for the sovereign identity and catalog aggregates."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from eidolon_data.db.base import Base, utc_now

JsonDict = dict[str, Any]


class OwnerRow(Base):
    __tablename__ = "owners"

    owner_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(128), default="")
    kind: Mapped[str] = mapped_column(String(32), default="person", index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    profile_json: Mapped[JsonDict] = mapped_column(default=dict)
    settings_json: Mapped[JsonDict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        CheckConstraint("kind IN ('person', 'family', 'team')", name="owner_kind"),
        CheckConstraint(
            "status IN ('active', 'archived', 'deleting')",
            name="owner_status",
        ),
    )


class CompanionRow(Base):
    __tablename__ = "companions"

    companion_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    display_name: Mapped[str] = mapped_column(String(128), default="")
    role: Mapped[str] = mapped_column(String(16), default="standard", index=True)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    current_genome_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey(
            "persona_genomes.genome_id",
            name="fk_companions_current_genome",
            ondelete="SET NULL",
            use_alter=True,
        ),
        index=True,
    )
    default_memory_realm_id: Mapped[str | None] = mapped_column(
        String(64),
        ForeignKey(
            "memory_realms.realm_id",
            name="fk_companions_default_memory_realm",
            ondelete="SET NULL",
            use_alter=True,
        ),
        index=True,
    )
    profile_json: Mapped[JsonDict] = mapped_column(default=dict)
    runtime_config_json: Mapped[JsonDict] = mapped_column(default=dict)
    metadata_json: Mapped[JsonDict] = mapped_column(default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("owner_id", "companion_id", name="uq_companions_owner_companion"),
        CheckConstraint(
            "role IN ('primary', 'standard', 'guard')",
            name="companion_role",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive', 'deleting')",
            name="companion_status",
        ),
        Index(
            "uq_companions_owner_primary",
            "owner_id",
            unique=True,
            sqlite_where=text("role = 'primary' AND status = 'active'"),
            postgresql_where=text("role = 'primary' AND status = 'active'"),
        ),
    )


class PersonaGenomeRow(Base):
    __tablename__ = "persona_genomes"

    genome_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    companion_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companions.companion_id", ondelete="CASCADE"), index=True
    )
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="committed", index=True)
    base_genome_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("persona_genomes.genome_id", ondelete="SET NULL"), index=True
    )
    schema_version: Mapped[str] = mapped_column(String(64), index=True)
    genome_hash: Mapped[str] = mapped_column(String(80), index=True)
    realizer_version: Mapped[str] = mapped_column(String(64))
    applied_event_id: Mapped[str | None] = mapped_column(String(64), index=True)
    source_json: Mapped[JsonDict] = mapped_column(default=dict)
    genome_json: Mapped[JsonDict] = mapped_column(default=dict)
    change_summary: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        UniqueConstraint("companion_id", "version", name="uq_persona_genomes_companion_version"),
        CheckConstraint("version > 0", name="persona_genome_version_positive"),
        CheckConstraint(
            "status IN ('proposed', 'committed', 'rejected', 'stale')",
            name="persona_genome_status",
        ),
        CheckConstraint(
            "schema_version = 'eidolon.persona_genome'",
            name="persona_genome_schema_current",
        ),
        CheckConstraint(
            "realizer_version = 'eidolon.persona_realizer'",
            name="persona_genome_realizer_current",
        ),
    )


class MemoryRealmRow(Base):
    """Catalog pointer to a Memory-owned realm; no memory payload lives here."""

    __tablename__ = "memory_realms"

    realm_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    owner_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("owners.owner_id", ondelete="CASCADE"), index=True
    )
    companion_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("companions.companion_id", ondelete="CASCADE"), index=True
    )
    engine: Mapped[str] = mapped_column(String(64), default="mempalace", index=True)
    engine_config_json: Mapped[JsonDict] = mapped_column(default=dict)
    policy_json: Mapped[JsonDict] = mapped_column(default=dict)
    status: Mapped[str] = mapped_column(String(16), default="active", index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    __table_args__ = (
        ForeignKeyConstraint(
            ["owner_id", "companion_id"],
            ["companions.owner_id", "companions.companion_id"],
            name="fk_memory_realms_owner_companion",
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive', 'deleting')",
            name="memory_realm_status",
        ),
    )
