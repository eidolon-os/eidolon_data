"""Durable Guard policy-action outbox repository."""

from __future__ import annotations

from datetime import timedelta
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError

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
        rows = await self.publish_many(
            actions=[
                {
                    "action_id": action_id,
                    "binding_id": binding_id,
                    "owner_id": owner_id,
                    "guard_companion_id": guard_companion_id,
                    "device_id": device_id,
                    "correlation_id": correlation_id,
                    "guard_epoch": guard_epoch,
                    "fact_type": fact_type,
                    "policy_id": policy_id,
                    "action": action,
                    "subscriber": subscriber,
                    "payload_json": payload_json or {},
                }
            ]
        )
        if rows is None:
            raise ValueError("guard policy action replay key already exists")
        return rows[0]

    async def publish_many(self, *, actions: list[dict]) -> list[GuardPolicyActionRow] | None:
        """Atomically append all actions for one fact, or report a replay conflict."""
        now = utc_now()
        rows = [
            GuardPolicyActionRow(
                **{
                    **action,
                    "status": "published",
                    "payload_json": action.get("payload_json") or {},
                    "next_attempt_at": now,
                    "replay_key": action.get("replay_key") or _replay_key(action),
                }
            )
            for action in actions
        ]
        async with self._session_factory() as session:
            session.add_all(rows)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                return None
            for row in rows:
                await session.refresh(row)
            return rows
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
        now = utc_now()
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(GuardPolicyActionRow)
                .where(
                    GuardPolicyActionRow.status == "published",
                    GuardPolicyActionRow.action == action,
                    GuardPolicyActionRow.command_id.is_(None),
                    GuardPolicyActionRow.delivery_dead_lettered_at.is_(None),
                    or_(
                        GuardPolicyActionRow.delivery_lease_expires_at.is_(None),
                        GuardPolicyActionRow.delivery_lease_expires_at <= now,
                    ),
                    or_(
                        GuardPolicyActionRow.next_attempt_at.is_(None),
                        GuardPolicyActionRow.next_attempt_at <= now,
                    ),
                )
                .order_by(GuardPolicyActionRow.published_at)
                .limit(limit)
            )
            return list(rows)

    async def claim_for_dispatch(
        self,
        action_id: str,
        *,
        lease_seconds: int = 30,
    ) -> GuardPolicyActionRow | None:
        """Atomically reserve a ready action for one worker before transport I/O."""
        now = utc_now()
        claim_token = uuid4().hex
        async with self._session_factory() as session:
            result = await session.execute(
                update(GuardPolicyActionRow)
                .where(
                    GuardPolicyActionRow.action_id == action_id,
                    GuardPolicyActionRow.status == "published",
                    GuardPolicyActionRow.command_id.is_(None),
                    GuardPolicyActionRow.delivery_dead_lettered_at.is_(None),
                    or_(
                        GuardPolicyActionRow.delivery_lease_expires_at.is_(None),
                        GuardPolicyActionRow.delivery_lease_expires_at <= now,
                    ),
                    or_(
                        GuardPolicyActionRow.next_attempt_at.is_(None),
                        GuardPolicyActionRow.next_attempt_at <= now,
                    ),
                )
                .values(
                    delivery_claim_token=claim_token,
                    delivery_lease_expires_at=now + timedelta(seconds=lease_seconds),
                    delivery_attempt_count=GuardPolicyActionRow.delivery_attempt_count + 1,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                await session.rollback()
                return None
            row = await session.get(GuardPolicyActionRow, action_id)
            await session.commit()
            await session.refresh(row)
            return row
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
        claim_token: str,
        command_id: str,
    ) -> GuardPolicyActionRow | None:
        now = utc_now()
        async with self._session_factory() as session:
            result = await session.execute(
                update(GuardPolicyActionRow)
                .where(
                    GuardPolicyActionRow.action_id == action_id,
                    GuardPolicyActionRow.status == "published",
                    GuardPolicyActionRow.command_id.is_(None),
                    GuardPolicyActionRow.delivery_claim_token == claim_token,
                )
                .values(
                    command_id=command_id,
                    dispatched_at=now,
                    delivery_claim_token=None,
                    delivery_lease_expires_at=None,
                    next_attempt_at=None,
                    last_error="",
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                await session.rollback()
                return None
            row = await session.get(GuardPolicyActionRow, action_id)
            await session.commit()
            await session.refresh(row)
            return row

    async def release_claim_after_error(
        self,
        action_id: str,
        *,
        claim_token: str,
        error: str,
        max_attempts: int,
        retry_delay_seconds: int,
    ) -> GuardPolicyActionRow | None:
        now = utc_now()
        async with self._session_factory() as session:
            row = await session.get(GuardPolicyActionRow, action_id)
            if row is None:
                return None
            if (
                row.status != "published"
                or row.command_id is not None
                or row.delivery_claim_token != claim_token
            ):
                return None
            row.delivery_claim_token = None
            row.delivery_lease_expires_at = None
            row.last_error = error[:1024]
            if row.delivery_attempt_count >= max_attempts:
                row.delivery_dead_lettered_at = now
                row.next_attempt_at = None
            else:
                row.next_attempt_at = now + timedelta(seconds=retry_delay_seconds)
            row.updated_at = now
            await session.commit()
            await session.refresh(row)
            return row

    async def list_dead_letter_body_deliveries(
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
                    GuardPolicyActionRow.delivery_dead_lettered_at.is_not(None),
                )
                .order_by(GuardPolicyActionRow.delivery_dead_lettered_at, GuardPolicyActionRow.action_id)
                .limit(limit)
            )
            return list(rows)

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
            row.delivery_claim_token = None
            row.delivery_lease_expires_at = None
            row.next_attempt_at = None
            row.updated_at = utc_now()
            await session.commit()
            await session.refresh(row)
            return row


def _replay_key(action: dict) -> str:
    values = (
        action["binding_id"],
        action["correlation_id"],
        str(action["guard_epoch"]),
        action.get("fact_type") or "",
        action["action"],
        action["subscriber"],
    )
    return sha256("\x1f".join(values).encode("utf-8")).hexdigest()
