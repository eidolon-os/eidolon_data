"""Guard policy-binding aggregate persistence.

This module stores low-frequency desired configuration only. Device admission,
presence, policy evaluation, command delivery, retry leases, and receipts are
runtime facts owned by Hub/Kernel/Channel/Guard runtime authorities.
"""

from __future__ import annotations

import re
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from eidolon_data.audit import governance_fact
from eidolon_data.db.base import utc_now
from eidolon_data.repositories.base import Repository
from eidolon_data.schema import CompanionRow, GuardBindingRow

_OPAQUE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


class GuardBindingsRepository(Repository):
    async def get(self, binding_id: str) -> GuardBindingRow | None:
        async with self._session_factory() as session:
            return await session.get(GuardBindingRow, binding_id)

    async def get_active_for_device(self, device_id: str) -> GuardBindingRow | None:
        async with self._session_factory() as session:
            return await session.scalar(
                select(GuardBindingRow).where(
                    GuardBindingRow.device_id == device_id,
                    GuardBindingRow.state == "active",
                )
            )

    async def list_for_owner(self, owner_id: str) -> list[GuardBindingRow]:
        async with self._session_factory() as session:
            return list(
                await session.scalars(
                    select(GuardBindingRow)
                    .where(GuardBindingRow.owner_id == owner_id)
                    .order_by(GuardBindingRow.created_at)
                )
            )

    async def bind(
        self,
        *,
        owner_id: str,
        guard_companion_id: str,
        device_id: str,
        policy_id: str = "silent_presence",
        policy_json: dict | None = None,
        binding_id: str | None = None,
    ) -> GuardBindingRow:
        _validate_opaque_id("device_id", device_id)
        _validate_opaque_id("policy_id", policy_id)
        binding_id = binding_id or f"gb_{uuid4().hex}"
        _validate_opaque_id("binding_id", binding_id)

        async with self._session_factory() as session, session.begin():
            companion = await session.get(CompanionRow, guard_companion_id)
            _validate_guard_companion(companion, owner_id)
            row = GuardBindingRow(
                binding_id=binding_id,
                owner_id=owner_id,
                guard_companion_id=guard_companion_id,
                device_id=device_id,
                state="active",
                policy_id=policy_id,
                policy_revision=1,
                policy_json=dict(policy_json or {}),
            )
            session.add(row)
            session.add(
                governance_fact(
                    owner_id=owner_id,
                    subject_type="guard_binding",
                    subject_id=binding_id,
                    action="guard.binding.created",
                    payload={
                        "guard_companion_id": guard_companion_id,
                        "device_id": device_id,
                        "policy_id": policy_id,
                        "policy_revision": 1,
                    },
                )
            )
            try:
                await session.flush()
            except IntegrityError as exc:
                raise ValueError(
                    "an active binding already exists for this device or guard companion"
                ) from exc
        return row

    async def update_policy(
        self,
        *,
        binding_id: str,
        expected_revision: int,
        policy_id: str,
        policy_json: dict,
    ) -> GuardBindingRow:
        _validate_opaque_id("policy_id", policy_id)
        if expected_revision < 1:
            raise ValueError("expected_revision must be positive")
        async with self._session_factory() as session, session.begin():
            row = await session.get(GuardBindingRow, binding_id)
            if row is None:
                raise KeyError(f"guard binding not found: {binding_id}")
            if row.state != "active":
                raise ValueError("only an active guard binding can be updated")
            if row.policy_revision != expected_revision:
                raise ValueError(
                    f"guard policy revision conflict: expected {expected_revision}, "
                    f"current {row.policy_revision}"
                )
            row.policy_id = policy_id
            row.policy_json = dict(policy_json)
            row.policy_revision += 1
            row.updated_at = utc_now()
            session.add(
                governance_fact(
                    owner_id=row.owner_id,
                    subject_type="guard_binding",
                    subject_id=row.binding_id,
                    action="guard.policy.updated",
                    payload={
                        "device_id": row.device_id,
                        "policy_id": row.policy_id,
                        "policy_revision": row.policy_revision,
                    },
                )
            )
        return row

    async def disable(
        self,
        binding_id: str,
        *,
        revoke: bool = False,
    ) -> GuardBindingRow:
        async with self._session_factory() as session, session.begin():
            row = await session.get(GuardBindingRow, binding_id)
            if row is None:
                raise KeyError(f"guard binding not found: {binding_id}")
            target = "revoked" if revoke else "disabled"
            if row.state == target:
                return row
            if row.state != "active":
                raise ValueError(f"cannot transition guard binding from {row.state} to {target}")
            now = utc_now()
            row.state = target
            row.updated_at = now
            if revoke:
                row.revoked_at = now
            else:
                row.disabled_at = now
            session.add(
                governance_fact(
                    owner_id=row.owner_id,
                    subject_type="guard_binding",
                    subject_id=row.binding_id,
                    action=f"guard.binding.{target}",
                    payload={"device_id": row.device_id},
                )
            )
        return row


def _validate_guard_companion(companion: CompanionRow | None, owner_id: str) -> None:
    if companion is None or companion.owner_id != owner_id:
        raise ValueError("guard companion not found for owner")
    if companion.status != "active":
        raise ValueError("guard companion is not active")
    if companion.role != "guard":
        raise ValueError("companion role must be guard")


def _validate_opaque_id(label: str, value: str) -> None:
    if not _OPAQUE_ID.fullmatch(value):
        raise ValueError(
            f"{label} must be 1-128 safe opaque characters (letters, numbers, _, ., :, or -)"
        )
