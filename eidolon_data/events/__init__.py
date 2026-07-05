"""Event contract for the sovereign ``events`` audit log.

This package is the single home for the *shared* event mechanism: the
event-type registry (catalog as code) and — from Phase 1 — the ``record_event``
facade. Subprojects import from here; they never hand-roll event rows.

See docs/跨系统/事件审计追踪补全方案.md.
"""

from __future__ import annotations

from eidolon_data.events.facade import build_event, new_event_id
from eidolon_data.events.registry import (
    CATALOG,
    OUTCOMES,
    SEVERITIES,
    SOURCES,
    TIERS,
    EventSpec,
    all_types,
    is_registered,
    is_valid_name,
    spec_for,
)

__all__ = [
    "CATALOG",
    "OUTCOMES",
    "SEVERITIES",
    "SOURCES",
    "TIERS",
    "EventSpec",
    "all_types",
    "build_event",
    "is_registered",
    "is_valid_name",
    "new_event_id",
    "spec_for",
]
