"""Shared test helpers for the event mechanism.

Ships with the package so every subproject can assert against written events
the same way instead of hand-rolling list comprehensions:

    from eidolon_data.testing import assert_event, assert_event_types

Kept dependency-light (operates on already-fetched ``EventRow`` lists, no DB
coupling) so it works with any ``events.list_for_*`` result.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from eidolon_data.schema.models import EventRow


def assert_event(events: Sequence[EventRow], *, event_type: str, **fields: Any) -> EventRow:
    """Assert exactly one event of ``event_type`` (optionally matching ``fields``) exists.

    ``fields`` are matched against attributes of :class:`EventRow`
    (``subject_type``, ``subject_id``, ``actor_type``, ``owner_id``, and — once
    added — ``source``/``outcome``/``companion_id``/…). Returns the match.
    """
    matches = [
        e
        for e in events
        if e.event_type == event_type
        and all(getattr(e, key) == value for key, value in fields.items())
    ]
    if not matches:
        seen = [e.event_type for e in events]
        raise AssertionError(
            f"no event {event_type!r} matching {fields or '{}'}; saw {seen}"
        )
    if len(matches) > 1:
        raise AssertionError(
            f"expected one event {event_type!r} matching {fields or '{}'}, found {len(matches)}"
        )
    return matches[0]


def assert_event_types(events: Sequence[EventRow], expected: Sequence[str]) -> None:
    """Assert the ordered sequence of ``event_type`` values equals ``expected``."""
    actual = [e.event_type for e in events]
    if actual != list(expected):
        raise AssertionError(f"event_type sequence {actual} != expected {list(expected)}")


__all__ = ["assert_event", "assert_event_types"]
