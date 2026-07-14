"""Durable convergence of Owner Face Profile revisions to Guard bindings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from eidolon_sdk.biz.guard import GuardOwnerFaceProfileApplyResult
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from eidolon_data.db.base import utc_now
from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import (
    GuardBindingRow,
    GuardOwnerFaceProfileDeliveryRow,
    OwnerFaceProfileRevisionRow,
)


@dataclass(frozen=True)
class OwnerFaceProfileDelivery:
    """Normalized delivery plus fields derived from its two foreign keys."""

    delivery_id: str
    binding_id: str
    profile_revision_id: str
    owner_id: str
    device_id: str
    profile_id: str
    profile_revision: int
    desired_state: str
    status: str
    command_id: str | None
    attempt_count: int
    last_error: str
    lease_expires_at: datetime | None
    dispatched_at: datetime | None
    applied_at: datetime | None
    created_at: datetime
    updated_at: datetime


def _delivery_select():
    return (
        select(
            GuardOwnerFaceProfileDeliveryRow,
            OwnerFaceProfileRevisionRow,
            GuardBindingRow,
        )
        .join(
            OwnerFaceProfileRevisionRow,
            OwnerFaceProfileRevisionRow.profile_revision_id
            == GuardOwnerFaceProfileDeliveryRow.profile_revision_id,
        )
        .join(
            GuardBindingRow,
            GuardBindingRow.binding_id == GuardOwnerFaceProfileDeliveryRow.binding_id,
        )
    )


def _view(
    row: GuardOwnerFaceProfileDeliveryRow,
    profile: OwnerFaceProfileRevisionRow,
    binding: GuardBindingRow,
) -> OwnerFaceProfileDelivery:
    return OwnerFaceProfileDelivery(
        delivery_id=row.delivery_id,
        binding_id=row.binding_id,
        profile_revision_id=row.profile_revision_id,
        owner_id=binding.owner_id,
        device_id=binding.device_id,
        profile_id=profile.profile_id,
        profile_revision=profile.revision,
        desired_state=profile.desired_state,
        status=row.status,
        command_id=row.command_id,
        attempt_count=row.attempt_count,
        last_error=row.last_error,
        lease_expires_at=row.lease_expires_at,
        dispatched_at=row.dispatched_at,
        applied_at=row.applied_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


async def _get_view(
    session: AsyncSession,
    *,
    delivery_id: str | None = None,
    command_id: str | None = None,
) -> OwnerFaceProfileDelivery | None:
    query = _delivery_select()
    if delivery_id is not None:
        query = query.where(GuardOwnerFaceProfileDeliveryRow.delivery_id == delivery_id)
    elif command_id is not None:
        query = query.where(GuardOwnerFaceProfileDeliveryRow.command_id == command_id)
    else:  # pragma: no cover - private contract
        raise ValueError("delivery_id or command_id is required")
    result = (await session.execute(query)).first()
    return _view(*result) if result is not None else None


class GuardOwnerFaceProfileDeliveriesRepository(Repository):
    async def list_ready(self, *, limit: int = 50) -> list[OwnerFaceProfileDelivery]:
        now = utc_now()
        async with self._session_factory() as session:
            await session.execute(
                update(GuardOwnerFaceProfileDeliveryRow)
                .where(
                    GuardOwnerFaceProfileDeliveryRow.status == "dispatching",
                    GuardOwnerFaceProfileDeliveryRow.lease_expires_at.is_not(None),
                    GuardOwnerFaceProfileDeliveryRow.lease_expires_at < now,
                )
                .values(
                    status="pending",
                    lease_expires_at=None,
                    last_error="dispatch lease expired",
                    updated_at=now,
                )
            )
            results = await session.execute(
                _delivery_select()
                .where(GuardOwnerFaceProfileDeliveryRow.status == "pending")
                .order_by(GuardOwnerFaceProfileDeliveryRow.created_at)
                .limit(limit)
            )
            await session.commit()
            return [_view(*result) for result in results]

    async def claim_for_dispatch(
        self,
        delivery_id: str,
        *,
        lease_seconds: int = 60,
    ) -> OwnerFaceProfileDelivery | None:
        now = utc_now()
        async with self._session_factory() as session:
            result = await session.execute(
                update(GuardOwnerFaceProfileDeliveryRow)
                .where(
                    GuardOwnerFaceProfileDeliveryRow.delivery_id == delivery_id,
                    GuardOwnerFaceProfileDeliveryRow.status == "pending",
                )
                .values(
                    status="dispatching",
                    attempt_count=GuardOwnerFaceProfileDeliveryRow.attempt_count + 1,
                    lease_expires_at=now + timedelta(seconds=lease_seconds),
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                await session.rollback()
                return None
            await session.commit()
            return await _get_view(session, delivery_id=delivery_id)

    async def mark_dispatched(
        self,
        delivery_id: str,
        *,
        command_id: str,
    ) -> OwnerFaceProfileDelivery | None:
        async with self._session_factory() as session:
            row = await session.get(GuardOwnerFaceProfileDeliveryRow, delivery_id)
            if row is None or row.status != "dispatching":
                return None
            row.status = "dispatched"
            row.command_id = command_id
            row.dispatched_at = utc_now()
            row.lease_expires_at = None
            row.last_error = ""
            row.updated_at = utc_now()
            await session.commit()
            return await _get_view(session, delivery_id=delivery_id)

    async def mark_retry(
        self,
        delivery_id: str,
        *,
        error: str,
    ) -> OwnerFaceProfileDelivery | None:
        async with self._session_factory() as session:
            row = await session.get(GuardOwnerFaceProfileDeliveryRow, delivery_id)
            if row is None or row.status != "dispatching":
                return None
            row.status = "pending"
            row.lease_expires_at = None
            row.last_error = error[:1024]
            row.updated_at = utc_now()
            await session.commit()
            return await _get_view(session, delivery_id=delivery_id)

    async def record_command_result(
        self,
        command_id: str,
        *,
        status: str,
        result_json: dict | None,
        error: str = "",
    ) -> OwnerFaceProfileDelivery | None:
        if status not in {"succeeded", "failed", "rejected", "expired", "timeout"}:
            return None
        async with self._session_factory() as session:
            delivery = await _get_view(session, command_id=command_id)
            if delivery is None or delivery.status == "superseded":
                return delivery
            row = await session.get(GuardOwnerFaceProfileDeliveryRow, delivery.delivery_id)
            if row is None:  # pragma: no cover - guarded by the join above
                return None
            if status == "succeeded":
                try:
                    result = GuardOwnerFaceProfileApplyResult.model_validate(result_json or {})
                except ValueError:
                    status = "failed"
                    error = "owner face profile result is invalid"
                else:
                    if (
                        result.binding_id != delivery.binding_id
                        or result.profile_id != delivery.profile_id
                        or result.profile_revision != delivery.profile_revision
                        or result.applied_state != delivery.desired_state
                    ):
                        status = "failed"
                        error = "owner face profile result does not match delivery"
            now = utc_now()
            row.status = "applied" if status == "succeeded" else "failed"
            row.applied_at = now if status == "succeeded" else None
            row.last_error = error[:1024]
            row.updated_at = now
            await session.commit()
            return await _get_view(session, delivery_id=delivery.delivery_id)

    async def retry_command_result(
        self,
        command_id: str,
        *,
        error: str,
        max_attempts: int = 5,
    ) -> OwnerFaceProfileDelivery | None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        async with self._session_factory() as session:
            delivery = await _get_view(session, command_id=command_id)
            if delivery is None or delivery.status != "dispatched":
                return delivery
            row = await session.get(GuardOwnerFaceProfileDeliveryRow, delivery.delivery_id)
            if row is None:  # pragma: no cover - guarded by the join above
                return None
            row.status = "pending" if row.attempt_count < max_attempts else "failed"
            if row.status == "pending":
                row.command_id = None
                row.dispatched_at = None
            row.lease_expires_at = None
            row.applied_at = None
            row.last_error = error[:1024]
            row.updated_at = utc_now()
            await session.commit()
            return await _get_view(session, delivery_id=delivery.delivery_id)

    async def list_for_binding(
        self, binding_id: str
    ) -> list[OwnerFaceProfileDelivery]:
        async with self._session_factory() as session:
            results = await session.execute(
                _delivery_select()
                .where(GuardOwnerFaceProfileDeliveryRow.binding_id == binding_id)
                .order_by(GuardOwnerFaceProfileDeliveryRow.created_at)
            )
            return [_view(*result) for result in results]
