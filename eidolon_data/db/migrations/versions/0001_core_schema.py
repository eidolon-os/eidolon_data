"""Create Eidolon Data core schema."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0001_core_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "owners",
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("profile_json", sa.JSON(), nullable=False),
        sa.Column("settings_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("owner_id", name=op.f("pk_owners")),
    )
    op.create_index(op.f("ix_owners_kind"), "owners", ["kind"], unique=False)
    op.create_index(op.f("ix_owners_status"), "owners", ["status"], unique=False)

    op.create_table(
        "companions",
        sa.Column("companion_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("current_genome_id", sa.String(length=64), nullable=True),
        sa.Column("default_memory_realm_id", sa.String(length=64), nullable=True),
        sa.Column("profile_json", sa.JSON(), nullable=False),
        sa.Column("runtime_config_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["owners.owner_id"], name=op.f("fk_companions_owner_id_owners"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("companion_id", name=op.f("pk_companions")),
    )
    op.create_index(op.f("ix_companions_current_genome_id"), "companions", ["current_genome_id"], unique=False)
    op.create_index(
        op.f("ix_companions_default_memory_realm_id"),
        "companions",
        ["default_memory_realm_id"],
        unique=False,
    )
    op.create_index(op.f("ix_companions_kind"), "companions", ["kind"], unique=False)
    op.create_index(op.f("ix_companions_owner_id"), "companions", ["owner_id"], unique=False)
    op.create_index(op.f("ix_companions_status"), "companions", ["status"], unique=False)

    op.create_table(
        "devices",
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=True),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(length=128), nullable=True),
        sa.Column("bound_companion_id", sa.String(length=64), nullable=True),
        sa.Column("interaction_mode", sa.String(length=64), nullable=True),
        sa.Column("auth_type", sa.String(length=32), nullable=True),
        sa.Column("secret_ref", sa.Text(), nullable=True),
        sa.Column("capabilities_json", sa.JSON(), nullable=False),
        sa.Column("network_json", sa.JSON(), nullable=False),
        sa.Column("access_policy_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["owners.owner_id"], name=op.f("fk_devices_owner_id_owners"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("device_id", name=op.f("pk_devices")),
    )
    op.create_index(op.f("ix_devices_bound_companion_id"), "devices", ["bound_companion_id"], unique=False)
    op.create_index(op.f("ix_devices_kind"), "devices", ["kind"], unique=False)
    op.create_index(op.f("ix_devices_owner_id"), "devices", ["owner_id"], unique=False)
    op.create_index(op.f("ix_devices_status"), "devices", ["status"], unique=False)

    op.create_table(
        "events",
        sa.Column("event_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("subject_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=96), nullable=False),
        sa.Column("actor_type", sa.String(length=32), nullable=False),
        sa.Column("actor_id", sa.String(length=128), nullable=True),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["owners.owner_id"], name=op.f("fk_events_owner_id_owners"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_events")),
    )
    op.create_index(op.f("ix_events_actor_id"), "events", ["actor_id"], unique=False)
    op.create_index(op.f("ix_events_actor_type"), "events", ["actor_type"], unique=False)
    op.create_index(op.f("ix_events_created_at"), "events", ["created_at"], unique=False)
    op.create_index(op.f("ix_events_event_type"), "events", ["event_type"], unique=False)
    op.create_index(op.f("ix_events_owner_id"), "events", ["owner_id"], unique=False)
    op.create_index(op.f("ix_events_subject_created"), "events", ["subject_type", "subject_id", "created_at"], unique=False)
    op.create_index(op.f("ix_events_subject_id"), "events", ["subject_id"], unique=False)
    op.create_index(op.f("ix_events_subject_type"), "events", ["subject_type"], unique=False)

    op.create_table(
        "jobs",
        sa.Column("job_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("companion_id", sa.String(length=64), nullable=True),
        sa.Column("conversation_id", sa.String(length=64), nullable=True),
        sa.Column("turn_id", sa.String(length=64), nullable=True),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_json", sa.JSON(), nullable=False),
        sa.Column("provider_ref_json", sa.JSON(), nullable=False),
        sa.Column("progress_json", sa.JSON(), nullable=False),
        sa.Column("result_json", sa.JSON(), nullable=False),
        sa.Column("error_json", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["owners.owner_id"], name=op.f("fk_jobs_owner_id_owners"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("job_id", name=op.f("pk_jobs")),
    )
    op.create_index(op.f("ix_jobs_companion_id"), "jobs", ["companion_id"], unique=False)
    op.create_index(op.f("ix_jobs_conversation_id"), "jobs", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_jobs_created_at"), "jobs", ["created_at"], unique=False)
    op.create_index(op.f("ix_jobs_kind"), "jobs", ["kind"], unique=False)
    op.create_index(op.f("ix_jobs_owner_id"), "jobs", ["owner_id"], unique=False)
    op.create_index(op.f("ix_jobs_provider"), "jobs", ["provider"], unique=False)
    op.create_index(op.f("ix_jobs_status"), "jobs", ["status"], unique=False)
    op.create_index(op.f("ix_jobs_turn_id"), "jobs", ["turn_id"], unique=False)
    op.create_index(op.f("ix_jobs_updated_at"), "jobs", ["updated_at"], unique=False)

    op.create_table(
        "conversations",
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("companion_id", sa.String(length=64), nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["companion_id"],
            ["companions.companion_id"],
            name=op.f("fk_conversations_companion_id_companions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["owners.owner_id"], name=op.f("fk_conversations_owner_id_owners"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("conversation_id", name=op.f("pk_conversations")),
    )
    op.create_index(op.f("ix_conversations_companion_id"), "conversations", ["companion_id"], unique=False)
    op.create_index(op.f("ix_conversations_device_id"), "conversations", ["device_id"], unique=False)
    op.create_index(op.f("ix_conversations_owner_id"), "conversations", ["owner_id"], unique=False)
    op.create_index(op.f("ix_conversations_owner_started"), "conversations", ["owner_id", "started_at"], unique=False)
    op.create_index(op.f("ix_conversations_owner_updated"), "conversations", ["owner_id", "updated_at"], unique=False)
    op.create_index(op.f("ix_conversations_status"), "conversations", ["status"], unique=False)

    op.create_table(
        "memory_realms",
        sa.Column("realm_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("companion_id", sa.String(length=64), nullable=False),
        sa.Column("engine", sa.String(length=64), nullable=False),
        sa.Column("engine_config_json", sa.JSON(), nullable=False),
        sa.Column("policy_json", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["companion_id"],
            ["companions.companion_id"],
            name=op.f("fk_memory_realms_companion_id_companions"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"], ["owners.owner_id"], name=op.f("fk_memory_realms_owner_id_owners"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("realm_id", name=op.f("pk_memory_realms")),
    )
    op.create_index(op.f("ix_memory_realms_companion_id"), "memory_realms", ["companion_id"], unique=False)
    op.create_index(op.f("ix_memory_realms_engine"), "memory_realms", ["engine"], unique=False)
    op.create_index(op.f("ix_memory_realms_owner_id"), "memory_realms", ["owner_id"], unique=False)
    op.create_index(op.f("ix_memory_realms_status"), "memory_realms", ["status"], unique=False)

    op.create_table(
        "persona_genomes",
        sa.Column("genome_id", sa.String(length=64), nullable=False),
        sa.Column("companion_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("base_genome_id", sa.String(length=64), nullable=True),
        sa.Column("source_json", sa.JSON(), nullable=False),
        sa.Column("genome_json", sa.JSON(), nullable=False),
        sa.Column("prompt_markdown", sa.Text(), nullable=False),
        sa.Column("evolution_state_json", sa.JSON(), nullable=False),
        sa.Column("change_summary", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["companion_id"],
            ["companions.companion_id"],
            name=op.f("fk_persona_genomes_companion_id_companions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("genome_id", name=op.f("pk_persona_genomes")),
        sa.UniqueConstraint("companion_id", "version", name="uq_persona_genomes_companion_version"),
    )
    op.create_index(op.f("ix_persona_genomes_base_genome_id"), "persona_genomes", ["base_genome_id"], unique=False)
    op.create_index(op.f("ix_persona_genomes_companion_id"), "persona_genomes", ["companion_id"], unique=False)
    op.create_index(op.f("ix_persona_genomes_status"), "persona_genomes", ["status"], unique=False)

    op.create_table(
        "turns",
        sa.Column("turn_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("device_id", sa.String(length=128), nullable=True),
        sa.Column("trigger", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("trace_json", sa.JSON(), nullable=False),
        sa.Column("metrics_json", sa.JSON(), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["conversations.conversation_id"],
            name=op.f("fk_turns_conversation_id_conversations"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("turn_id", name=op.f("pk_turns")),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_turns_conversation_seq"),
    )
    op.create_index(op.f("ix_turns_conversation_id"), "turns", ["conversation_id"], unique=False)
    op.create_index(op.f("ix_turns_device_id"), "turns", ["device_id"], unique=False)
    op.create_index(op.f("ix_turns_status"), "turns", ["status"], unique=False)

    op.create_table(
        "messages",
        sa.Column("message_id", sa.String(length=64), nullable=False),
        sa.Column("turn_id", sa.String(length=64), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_type", sa.String(length=64), nullable=False),
        sa.Column("visibility", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["turn_id"], ["turns.turn_id"], name=op.f("fk_messages_turn_id_turns"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("message_id", name=op.f("pk_messages")),
        sa.UniqueConstraint("turn_id", "seq", name="uq_messages_turn_seq"),
    )
    op.create_index(op.f("ix_messages_role"), "messages", ["role"], unique=False)
    op.create_index(op.f("ix_messages_turn_created"), "messages", ["turn_id", "created_at"], unique=False)
    op.create_index(op.f("ix_messages_turn_id"), "messages", ["turn_id"], unique=False)
    op.create_index(op.f("ix_messages_visibility"), "messages", ["visibility"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_messages_visibility"), table_name="messages")
    op.drop_index(op.f("ix_messages_turn_id"), table_name="messages")
    op.drop_index(op.f("ix_messages_turn_created"), table_name="messages")
    op.drop_index(op.f("ix_messages_role"), table_name="messages")
    op.drop_table("messages")

    op.drop_index(op.f("ix_turns_status"), table_name="turns")
    op.drop_index(op.f("ix_turns_device_id"), table_name="turns")
    op.drop_index(op.f("ix_turns_conversation_id"), table_name="turns")
    op.drop_table("turns")

    op.drop_index(op.f("ix_persona_genomes_status"), table_name="persona_genomes")
    op.drop_index(op.f("ix_persona_genomes_companion_id"), table_name="persona_genomes")
    op.drop_index(op.f("ix_persona_genomes_base_genome_id"), table_name="persona_genomes")
    op.drop_table("persona_genomes")

    op.drop_index(op.f("ix_memory_realms_status"), table_name="memory_realms")
    op.drop_index(op.f("ix_memory_realms_owner_id"), table_name="memory_realms")
    op.drop_index(op.f("ix_memory_realms_engine"), table_name="memory_realms")
    op.drop_index(op.f("ix_memory_realms_companion_id"), table_name="memory_realms")
    op.drop_table("memory_realms")

    op.drop_index(op.f("ix_conversations_status"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_owner_updated"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_owner_started"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_owner_id"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_device_id"), table_name="conversations")
    op.drop_index(op.f("ix_conversations_companion_id"), table_name="conversations")
    op.drop_table("conversations")

    op.drop_index(op.f("ix_jobs_updated_at"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_turn_id"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_status"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_provider"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_owner_id"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_kind"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_created_at"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_conversation_id"), table_name="jobs")
    op.drop_index(op.f("ix_jobs_companion_id"), table_name="jobs")
    op.drop_table("jobs")

    op.drop_index(op.f("ix_events_subject_type"), table_name="events")
    op.drop_index(op.f("ix_events_subject_id"), table_name="events")
    op.drop_index(op.f("ix_events_subject_created"), table_name="events")
    op.drop_index(op.f("ix_events_owner_id"), table_name="events")
    op.drop_index(op.f("ix_events_event_type"), table_name="events")
    op.drop_index(op.f("ix_events_created_at"), table_name="events")
    op.drop_index(op.f("ix_events_actor_type"), table_name="events")
    op.drop_index(op.f("ix_events_actor_id"), table_name="events")
    op.drop_table("events")

    op.drop_index(op.f("ix_devices_status"), table_name="devices")
    op.drop_index(op.f("ix_devices_owner_id"), table_name="devices")
    op.drop_index(op.f("ix_devices_kind"), table_name="devices")
    op.drop_index(op.f("ix_devices_bound_companion_id"), table_name="devices")
    op.drop_table("devices")

    op.drop_index(op.f("ix_companions_status"), table_name="companions")
    op.drop_index(op.f("ix_companions_owner_id"), table_name="companions")
    op.drop_index(op.f("ix_companions_kind"), table_name="companions")
    op.drop_index(op.f("ix_companions_default_memory_realm_id"), table_name="companions")
    op.drop_index(op.f("ix_companions_current_genome_id"), table_name="companions")
    op.drop_table("companions")

    op.drop_index(op.f("ix_owners_status"), table_name="owners")
    op.drop_index(op.f("ix_owners_kind"), table_name="owners")
    op.drop_table("owners")
