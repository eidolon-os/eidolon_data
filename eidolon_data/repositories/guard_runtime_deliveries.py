"""Durable delivery queue for Guard runtime desired state."""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import or_, select, update

from eidolon_data.db.base import utc_now
from eidolon_data.repositories.base import Repository
from eidolon_data.schema.models import GuardRuntimeDeliveryRow

_PENDING = "pending"
_DISPATCHING = "dispatching"
_DISPATCHED = "dispatched"
_APPLIED = "applied"
_FAILED = "failed"
_SUPERSEDED = "superseded"


class GuardRuntimeDeliveriesRepository(Repository):
    async def list_ready(self, *, limit: int = 50) -> list[GuardRuntimeDeliveryRow]:
        """Return pending work and reclaim expired dispatch leases.

        The caller must subsequently claim each row.  Recovering a lease here
        makes a Hub crash between claim and LiveKit send retryable without a
        process-local timer or an Admin-to-Hub callback.
        """
        now = utc_now()
        async with self._session_factory() as session:
            await session.execute(
                update(GuardRuntimeDeliveryRow)
                .where(
                    GuardRuntimeDeliveryRow.status == _DISPATCHING,
                    GuardRuntimeDeliveryRow.lease_expires_at.is_not(None),
                    GuardRuntimeDeliveryRow.lease_expires_at < now,
                )
                .values(
                    status=_PENDING,
                    lease_expires_at=None,
                    last_error="dispatch lease expired",
                    updated_at=now,
                )
            )
            rows = await session.scalars(
                select(GuardRuntimeDeliveryRow)
                .where(GuardRuntimeDeliveryRow.status == _PENDING)
                .order_by(GuardRuntimeDeliveryRow.created_at)
                .limit(limit)
            )
            await session.commit()
            return list(rows)

    async def claim_for_dispatch(
        self,
        delivery_id: str,
        *,
        lease_seconds: int = 60,
    ) -> GuardRuntimeDeliveryRow | None:
        now = utc_now()
        lease_expires_at = now + timedelta(seconds=lease_seconds)
        async with self._session_factory() as session:
            result = await session.execute(
                update(GuardRuntimeDeliveryRow)
                .where(
                    GuardRuntimeDeliveryRow.delivery_id == delivery_id,
                    GuardRuntimeDeliveryRow.status == _PENDING,
                )
                .values(
                    status=_DISPATCHING,
                    attempt_count=GuardRuntimeDeliveryRow.attempt_count + 1,
                    lease_expires_at=lease_expires_at,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                await session.rollback()
                return None
            row = await session.get(GuardRuntimeDeliveryRow, delivery_id)
            await session.commit()
            return row

    async def mark_dispatched(self, delivery_id: str, *, command_id: str) -> GuardRuntimeDeliveryRow | None:
        now = utc_now()
        async with self._session_factory() as session:
            row = await session.get(GuardRuntimeDeliveryRow, delivery_id)
            if row is None or row.status != _DISPATCHING:
                return None
            row.status = _DISPATCHED
            row.command_id = command_id
            row.dispatched_at = now
            row.lease_expires_at = None
            row.last_error = ""
            row.updated_at = now
            await session.commit()
            await session.refresh(row)
            return row

    async def mark_retry(self, delivery_id: str, *, error: str) -> GuardRuntimeDeliveryRow | None:
        now = utc_now()
        async with self._session_factory() as session:
            row = await session.get(GuardRuntimeDeliveryRow, delivery_id)
            if row is None or row.status != _DISPATCHING:
                return None
            row.status = _PENDING
            row.lease_expires_at = None
            row.last_error = error[:1024]
            row.updated_at = now
            await session.commit()
            await session.refresh(row)
            return row

    async def record_command_result(
        self,
        command_id: str,
        *,
        status: str,
        result_json: dict | None,
        error: str = "",
    ) -> GuardRuntimeDeliveryRow | None:
        """Persist the terminal device result associated with a dispatched sync."""
        if status not in {"succeeded", "failed", "rejected", "expired", "timeout"}:
            return None
        now = utc_now()
        async with self._session_factory() as session:
            row = await session.scalar(
                select(GuardRuntimeDeliveryRow).where(GuardRuntimeDeliveryRow.command_id == command_id)
            )
            if row is None:
                return None
            if status == "succeeded":
                actual_binding_id = (result_json or {}).get("binding_id")
                actual_state = (result_json or {}).get("desired_runtime_state")
                actual_revision = (result_json or {}).get("runtime_revision")
                if (
                    actual_binding_id != row.binding_id
                    or actual_state != row.desired_runtime_state
                    or not isinstance(actual_revision, int)
                    or actual_revision < row.runtime_revision
                ):
                    status = "failed"
                    error = "guard runtime result does not match delivery"
            row.status = _APPLIED if status == "succeeded" else _FAILED
            row.applied_at = now if status == "succeeded" else None
            row.last_error = error[:1024]
            row.result_json = result_json
            row.updated_at = now
            await session.commit()
            await session.refresh(row)
            return row

    async def list_for_binding(self, binding_id: str) -> list[GuardRuntimeDeliveryRow]:
        async with self._session_factory() as session:
            rows = await session.scalars(
                select(GuardRuntimeDeliveryRow)
                .where(GuardRuntimeDeliveryRow.binding_id == binding_id)
                .order_by(GuardRuntimeDeliveryRow.created_at)
            )
            return list(rows)


def enqueue_runtime_delivery(session, binding) -> GuardRuntimeDeliveryRow:
    """Append a desired-state revision inside the binding mutation transaction."""
    row = GuardRuntimeDeliveryRow(
        delivery_id=f"grd_{uuid4().hex}",
        binding_id=binding.binding_id,
        owner_id=binding.owner_id,
        device_id=binding.device_id,
        runtime_revision=binding.runtime_revision,
        desired_runtime_state=binding.desired_runtime_state,
        status=_PENDING,
    )
    session.add(row)
    return row
