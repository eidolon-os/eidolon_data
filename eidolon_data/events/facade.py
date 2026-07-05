"""The single, contract-carrying way to construct an event row.

``build_event`` validates against the registry and fills classification
defaults (tier/source/severity/outcome) from the :class:`EventSpec`, so callers
only supply what is business-specific. Two entry points share it:

- ``build_event(...)`` returns an unpersisted :class:`EventRow` — use it for
  **same-transaction** governance/state-transition events: ``session.add(build_event(...))``.
- ``EventsRepository.record_event(...)`` builds and persists in its own session —
  use it for standalone / fire-and-forget emits.

Do not construct ``EventRow`` by hand elsewhere; that reintroduces the scattered
writes this facade exists to remove (see docs §5).
"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from eidolon_data.db.base import utc_now
from eidolon_data.events.registry import (
    OUTCOMES,
    SEVERITIES,
    SOURCES,
    TIERS,
    spec_for,
)
from eidolon_data.schema.models import EventRow


def new_event_id() -> str:
    """Canonical event id. Deterministic ids (for idempotency) are also allowed."""
    return f"evt_{uuid4().hex}"


def build_event(
    *,
    event_type: str,
    owner_id: str,
    subject_type: str,
    subject_id: str,
    event_id: str | None = None,
    companion_id: str | None = None,
    actor_type: str = "system",
    actor_id: str | None = None,
    event_class: str | None = None,
    source: str | None = None,
    severity: str | None = None,
    outcome: str | None = None,
    reason: str | None = None,
    trace_id: str | None = None,
    data_classification: str = "safe",
    schema_version: int = 1,
    payload_json: dict | None = None,
    occurred_at: datetime | None = None,
    strict: bool = True,
) -> EventRow:
    """Build a validated, defaults-filled :class:`EventRow` (not persisted).

    Unregistered ``event_type`` raises when ``strict`` (register it in the
    catalog first). Classification fields default from the spec when omitted.
    """
    spec = spec_for(event_type)
    if spec is None and strict:
        raise ValueError(
            f"unregistered event_type {event_type!r}; declare it in eidolon_data.events.registry"
        )

    payload = dict(payload_json or {})
    if spec is not None:
        missing = [key for key in spec.required_payload if key not in payload]
        if missing:
            raise ValueError(f"event {event_type!r} missing required payload keys: {missing}")

    resolved_class = event_class or (spec.tier if spec else "audit")
    resolved_source = source or (spec.source if spec else "data")
    resolved_severity = severity or (spec.default_severity if spec else "info")
    resolved_outcome = outcome or (spec.default_outcome if spec else "success")

    for value, allowed, label in (
        (resolved_class, TIERS, "event_class"),
        (resolved_source, SOURCES, "source"),
        (resolved_severity, SEVERITIES, "severity"),
        (resolved_outcome, OUTCOMES, "outcome"),
    ):
        if value not in allowed:
            raise ValueError(f"invalid {label} {value!r}; expected one of {allowed}")

    return EventRow(
        event_id=event_id or new_event_id(),
        owner_id=owner_id,
        companion_id=companion_id,
        subject_type=subject_type,
        subject_id=subject_id,
        event_type=event_type,
        event_class=resolved_class,
        source=resolved_source,
        severity=resolved_severity,
        outcome=resolved_outcome,
        reason=reason,
        actor_type=actor_type,
        actor_id=actor_id,
        trace_id=trace_id,
        data_classification=data_classification,
        schema_version=schema_version,
        payload_json=payload,
        occurred_at=occurred_at or utc_now(),
    )


__all__ = ["build_event", "new_event_id"]
