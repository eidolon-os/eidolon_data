"""Conversation, turn, and message repositories."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select

from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import CompanionRow, ConversationRow, DeviceRow, MessageRow, TurnRow


class ConversationsRepository(Repository):
    async def create_conversation(
        self,
        *,
        conversation_id: str,
        owner_id: str,
        companion_id: str,
        device_id: str | None = None,
        title: str | None = None,
        status: str = "active",
        metadata_json: dict | None = None,
    ) -> ConversationRow:
        async with self._session_factory() as session:
            companion = await session.get(CompanionRow, companion_id)
            if companion is None:
                raise KeyError(f"companion not found: {companion_id}")
            if companion.owner_id != owner_id:
                raise ValueError(
                    f"companion {companion_id!r} belongs to owner {companion.owner_id!r}, not {owner_id!r}"
                )
            if companion.status != "active":
                raise ValueError(f"companion {companion_id!r} is not active")
            if device_id is not None:
                device = await session.get(DeviceRow, device_id)
                if device is None:
                    raise KeyError(f"device not found: {device_id}")
                if device.owner_id != owner_id:
                    raise ValueError(
                        f"device {device_id!r} belongs to owner {device.owner_id!r}, not {owner_id!r}"
                    )
                if device.bound_companion_id != companion_id:
                    raise ValueError(
                        f"device {device_id!r} is bound to companion {device.bound_companion_id!r}, not {companion_id!r}"
                    )
                if device.status in {"disabled", "revoked"}:
                    raise ValueError(f"device {device_id!r} is {device.status}")
            row = ConversationRow(
                conversation_id=conversation_id,
                owner_id=owner_id,
                companion_id=companion_id,
                device_id=device_id,
                title=title,
                status=status,
                metadata_json=metadata_json or {},
            )
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def append_turn(
        self,
        *,
        turn_id: str,
        conversation_id: str,
        seq: int,
        device_id: str | None = None,
        trigger: str = "user",
        status: str = "completed",
        finished_at: datetime | None = None,
        trace_json: dict | None = None,
        metrics_json: dict | None = None,
        metadata_json: dict | None = None,
    ) -> TurnRow:
        row = TurnRow(
            turn_id=turn_id,
            conversation_id=conversation_id,
            seq=seq,
            device_id=device_id,
            trigger=trigger,
            status=status,
            finished_at=finished_at,
            trace_json=trace_json or {},
            metrics_json=metrics_json or {},
            metadata_json=metadata_json or {},
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def append_message(
        self,
        *,
        message_id: str,
        turn_id: str,
        role: str,
        content: str,
        seq: int = 0,
        content_type: str = "text/plain",
        visibility: str = "normal",
        metadata_json: dict | None = None,
    ) -> MessageRow:
        row = MessageRow(
            message_id=message_id,
            turn_id=turn_id,
            seq=seq,
            role=role,
            content=content,
            content_type=content_type,
            visibility=visibility,
            metadata_json=metadata_json or {},
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def list_messages_for_conversation(self, conversation_id: str) -> list[MessageRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(MessageRow)
                .join(TurnRow, MessageRow.turn_id == TurnRow.turn_id)
                .where(TurnRow.conversation_id == conversation_id)
                .order_by(TurnRow.seq, MessageRow.seq, MessageRow.created_at)
            )
            return list(rows)

    async def list_for_owner(self, owner_id: str, *, limit: int = 100) -> list[ConversationRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(ConversationRow)
                .where(ConversationRow.owner_id == owner_id)
                .order_by(ConversationRow.updated_at.desc())
                .limit(limit)
            )
            return list(rows)
