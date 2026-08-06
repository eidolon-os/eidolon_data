"""Add runtime context and body command audit tables."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0004_runtime_context_body_commands"
down_revision = "0003_owner_companion_constraints"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_table("messages")
    op.drop_table("turns")
    op.drop_table("conversations")

    op.create_table(
        "runtime_sessions",
        sa.Column("session_id", sa.String(length=128), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("companion_id", sa.String(length=64), nullable=True),
        sa.Column("source_device_id", sa.String(length=128), nullable=True),
        sa.Column("transport", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.owner_id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["owner_id", "companion_id"],
            ["companions.owner_id", "companions.companion_id"],
            name="fk_runtime_sessions_owner_companion",
        ),
        sa.PrimaryKeyConstraint("session_id"),
    )
    op.create_index("ix_runtime_sessions_companion_id", "runtime_sessions", ["companion_id"])
    op.create_index("ix_runtime_sessions_last_seen_at", "runtime_sessions", ["last_seen_at"])
    op.create_index("ix_runtime_sessions_owner_id", "runtime_sessions", ["owner_id"])
    op.create_index("ix_runtime_sessions_owner_last_seen", "runtime_sessions", ["owner_id", "last_seen_at"])
    op.create_index("ix_runtime_sessions_source_device_id", "runtime_sessions", ["source_device_id"])
    op.create_index("ix_runtime_sessions_started_at", "runtime_sessions", ["started_at"])
    op.create_index("ix_runtime_sessions_status", "runtime_sessions", ["status"])
    op.create_index("ix_runtime_sessions_transport", "runtime_sessions", ["transport"])

    op.create_table(
        "body_commands",
        sa.Column("command_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=True),
        sa.Column("companion_id", sa.String(length=64), nullable=True),
        sa.Column("runtime_session_id", sa.String(length=128), nullable=True),
        sa.Column("device_id", sa.String(length=128), nullable=False),
        sa.Column("source_device_id", sa.String(length=128), nullable=True),
        sa.Column("topic", sa.String(length=128), nullable=False),
        sa.Column("op", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=False),
        sa.Column("envelope_json", sa.JSON(), nullable=False),
        sa.Column("ack_json", sa.JSON(), nullable=True),
        sa.Column("result_json", sa.JSON(), nullable=True),
        sa.Column("ttl_ms", sa.Integer(), nullable=False),
        sa.Column("qos", sa.String(length=32), nullable=False),
        sa.Column("priority", sa.String(length=32), nullable=False),
        sa.Column("error", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["owner_id"], ["owners.owner_id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["runtime_session_id"],
            ["runtime_sessions.session_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("command_id"),
    )
    op.create_index("ix_body_commands_companion_id", "body_commands", ["companion_id"])
    op.create_index("ix_body_commands_created_at", "body_commands", ["created_at"])
    op.create_index("ix_body_commands_device_created", "body_commands", ["device_id", "created_at"])
    op.create_index("ix_body_commands_device_id", "body_commands", ["device_id"])
    op.create_index("ix_body_commands_expires_at", "body_commands", ["expires_at"])
    op.create_index("ix_body_commands_op", "body_commands", ["op"])
    op.create_index("ix_body_commands_owner_created", "body_commands", ["owner_id", "created_at"])
    op.create_index("ix_body_commands_owner_id", "body_commands", ["owner_id"])
    op.create_index("ix_body_commands_runtime_session_id", "body_commands", ["runtime_session_id"])
    op.create_index("ix_body_commands_source_device_id", "body_commands", ["source_device_id"])
    op.create_index("ix_body_commands_status", "body_commands", ["status"])
    op.create_index("ix_body_commands_topic", "body_commands", ["topic"])
    op.create_index("ix_body_commands_updated_at", "body_commands", ["updated_at"])

    op.create_table(
        "conversations",
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("owner_id", sa.String(length=64), nullable=False),
        sa.Column("companion_id", sa.String(length=64), nullable=False),
        sa.Column("runtime_session_id", sa.String(length=128), nullable=True),
        sa.Column("source_device_id", sa.String(length=128), nullable=True),
        sa.Column("title", sa.String(length=256), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["owner_id", "companion_id"],
            ["companions.owner_id", "companions.companion_id"],
            name="fk_conversations_owner_companion",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_session_id"],
            ["runtime_sessions.session_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("conversation_id"),
    )
    op.create_index("ix_conversations_companion_id", "conversations", ["companion_id"])
    op.create_index("ix_conversations_owner_id", "conversations", ["owner_id"])
    op.create_index("ix_conversations_owner_started", "conversations", ["owner_id", "started_at"])
    op.create_index("ix_conversations_owner_updated", "conversations", ["owner_id", "updated_at"])
    op.create_index("ix_conversations_runtime_session_id", "conversations", ["runtime_session_id"])
    op.create_index("ix_conversations_source_device_id", "conversations", ["source_device_id"])
    op.create_index("ix_conversations_status", "conversations", ["status"])

    op.create_table(
        "turns",
        sa.Column("turn_id", sa.String(length=64), nullable=False),
        sa.Column("conversation_id", sa.String(length=64), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("runtime_session_id", sa.String(length=128), nullable=True),
        sa.Column("source_device_id", sa.String(length=128), nullable=True),
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
            name="fk_turns_conversation_id_conversations",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["runtime_session_id"],
            ["runtime_sessions.session_id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("turn_id"),
        sa.UniqueConstraint("conversation_id", "seq", name="uq_turns_conversation_seq"),
    )
    op.create_index("ix_turns_conversation_id", "turns", ["conversation_id"])
    op.create_index("ix_turns_runtime_session_id", "turns", ["runtime_session_id"])
    op.create_index("ix_turns_source_device_id", "turns", ["source_device_id"])
    op.create_index("ix_turns_status", "turns", ["status"])

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
            ["turn_id"],
            ["turns.turn_id"],
            name="fk_messages_turn_id_turns",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("message_id"),
        sa.UniqueConstraint("turn_id", "seq", name="uq_messages_turn_seq"),
    )
    op.create_index("ix_messages_created_at", "messages", ["created_at"])
    op.create_index("ix_messages_role", "messages", ["role"])
    op.create_index("ix_messages_turn_created", "messages", ["turn_id", "created_at"])
    op.create_index("ix_messages_turn_id", "messages", ["turn_id"])
    op.create_index("ix_messages_visibility", "messages", ["visibility"])


def downgrade() -> None:
    op.drop_table("messages")
    op.drop_table("turns")
    op.drop_table("conversations")
    op.drop_table("body_commands")
    op.drop_table("runtime_sessions")
