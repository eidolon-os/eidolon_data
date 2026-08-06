"""Explicit constructors for System Data governance facts.

There is intentionally no generic event catalog. Application services name
their durable governance transition at the transaction boundary; telemetry and
runtime receipts belong to their producing authorities.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from eidolon_data.audit.outbox import AuditOutboxRepository
from eidolon_data.schema import AuditOutboxRow


def governance_fact(
    *,
    owner_id: str,
    subject_type: str,
    subject_id: str,
    action: str,
    payload: dict[str, Any] | None = None,
    event_id: str | None = None,
    outcome: str = "success",
    severity: str = "info",
    reason: str | None = None,
    trace_id: str | None = None,
    data_classification: str = "safe",
    occurred_at: datetime | None = None,
) -> AuditOutboxRow:
    """Build one outbox row for insertion in the caller's domain transaction."""

    return AuditOutboxRepository.build_row(
        producer="eidolon-system-data",
        category="governance",
        owner_id=owner_id,
        subject_type=subject_type,
        subject_id=subject_id,
        action=action,
        event_id=event_id,
        outcome=outcome,
        severity=severity,
        reason=reason,
        trace_id=trace_id,
        data_classification=data_classification,
        payload=payload,
        occurred_at=occurred_at,
    )
