"""Durable Guard policy-action outbox repository."""

from __future__ import annotations

from sqlalchemy import select

from eidolon_data.db.base import utc_now
from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import GuardPolicyActionRow


class GuardPolicyActionsRepository(Repository):
    async def publish(
        self,
        *,
        action_id: str,
        binding_id: str,
        owner_id: str,
        guard_companion_id: str,
        device_id: str,
        correlation_id: str,
        guard_epoch: int,
        policy_id: str,
        action: str,
        subscriber: str,
        fact_type: str = "",
        payload_json: dict | None = None,
    ) -> GuardPolicyActionRow:
        row = GuardPolicyActionRow(
            action_id=action_id,
            binding_id=binding_id,
            owner_id=owner_id,
            guard_companion_id=guard_companion_id,
            device_id=device_id,
            correlation_id=correlation_id,
            guard_epoch=guard_epoch,
            fact_type=fact_type,
            policy_id=policy_id,
            action=action,
            subscriber=subscriber,
            status="published",
            payload_json=payload_json or {},
        )
        async with self._session_factory() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row

    async def get(self, action_id: str) -> GuardPolicyActionRow | None:
        async with self._session_factory() as session:
            return await session.get(GuardPolicyActionRow, action_id)

    async def get_by_command_id(self, command_id: str) -> GuardPolicyActionRow | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(GuardPolicyActionRow).where(GuardPolicyActionRow.command_id == command_id)
            )

    async def list_for_fact(
        self,
        *,
        binding_id: str,
        correlation_id: str,
        guard_epoch: int,
        fact_type: str,
    ) -> list[GuardPolicyActionRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(GuardPolicyActionRow)
                .where(
                    GuardPolicyActionRow.binding_id == binding_id,
                    GuardPolicyActionRow.correlation_id == correlation_id,
                    GuardPolicyActionRow.guard_epoch == guard_epoch,
                    GuardPolicyActionRow.fact_type == fact_type,
                )
                .order_by(GuardPolicyActionRow.published_at, GuardPolicyActionRow.action_id)
            )
            return list(rows)

    async def list_pending(
        self,
        *,
        owner_id: str | None = None,
        subscriber: str | None = None,
    ) -> list[GuardPolicyActionRow]:
        async with self._session_factory() as session:
            statement = (
                select(GuardPolicyActionRow)
                .where(GuardPolicyActionRow.status == "published")
                .order_by(GuardPolicyActionRow.published_at)
            )
            if owner_id:
                statement = statement.where(GuardPolicyActionRow.owner_id == owner_id)
            if subscriber:
                statement = statement.where(GuardPolicyActionRow.subscriber == subscriber)
            rows = await session.scalars(statement)
            return list(rows)

    async def list_ready_body_deliveries(
        self,
        *,
        action: str,
        limit: int = 50,
    ) -> list[GuardPolicyActionRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(GuardPolicyActionRow)
                .where(
                    GuardPolicyActionRow.status == "published",
                    GuardPolicyActionRow.action == action,
                    GuardPolicyActionRow.command_id.is_(None),
                )
                .order_by(GuardPolicyActionRow.published_at)
                .limit(limit)
            )
            return list(rows)

    async def list_dispatched_body_deliveries(
        self,
        *,
        action: str,
        limit: int = 50,
    ) -> list[GuardPolicyActionRow]:
        """Return body actions awaiting a terminal command result."""
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(GuardPolicyActionRow)
                .where(
                    GuardPolicyActionRow.status == "published",
                    GuardPolicyActionRow.action == action,
                    GuardPolicyActionRow.command_id.is_not(None),
                )
                .order_by(GuardPolicyActionRow.dispatched_at, GuardPolicyActionRow.action_id)
                .limit(limit)
            )
            return list(rows)

    async def mark_dispatched(
        self,
        action_id: str,
        *,
        command_id: str,
    ) -> GuardPolicyActionRow | None:
        async with self._session_factory() as session:
            row = await session.get(GuardPolicyActionRow, action_id)
            if row is None:
                return None
            if row.status != "published":
                return row
            if row.command_id and row.command_id != command_id:
                raise ValueError("guard action already mapped to a different command")
            row.command_id = command_id
            row.dispatched_at = utc_now()
            row.updated_at = utc_now()
            row.last_error = ""
            await session.commit()
            await session.refresh(row)
            return row

    async def record_delivery_error(
        self,
        action_id: str,
        *,
        error: str,
    ) -> GuardPolicyActionRow | None:
        async with self._session_factory() as session:
            row = await session.get(GuardPolicyActionRow, action_id)
            if row is None:
                return None
            if row.status != "published":
                return row
            row.delivery_attempt_count += 1
            row.last_error = error
            row.updated_at = utc_now()
            await session.commit()
            await session.refresh(row)
            return row

    async def acknowledge(
        self,
        action_id: str,
        *,
        status: str,
        ack_json: dict,
    ) -> GuardPolicyActionRow:
        if status not in {"accepted", "completed", "failed"}:
            raise ValueError("invalid guard action acknowledgement status")
        async with self._session_factory() as session:
            row = await session.get(GuardPolicyActionRow, action_id)
            if row is None:
                raise KeyError(f"guard policy action not found: {action_id}")
            requested_status = "acknowledged" if status in {"accepted", "completed"} else "failed"
            if row.status != "published":
                if row.status == requested_status:
                    return row
                raise ValueError("guard action terminal acknowledgement cannot be overwritten")
            row.status = requested_status
            row.ack_json = ack_json
            row.acknowledged_at = utc_now()
            row.updated_at = utc_now()
            await session.commit()
            await session.refresh(row)
            return row
