"""L1 — event catalog conformance (see docs §14).

Pure checks on the registry: naming convention, valid enums, no duplicates,
lookup helpers, and that the known taxonomy drift stays explicitly flagged.
No DB fixture needed.
"""

from __future__ import annotations

import pytest

from eidolon_data.events import (
    CATALOG,
    OUTCOMES,
    SEVERITIES,
    SOURCES,
    TIERS,
    all_types,
    is_registered,
    is_valid_name,
    spec_for,
)


def test_catalog_is_non_empty():
    assert CATALOG, "event catalog must not be empty"
    assert all_types() == sorted(CATALOG)


@pytest.mark.parametrize("event_type", sorted(CATALOG))
def test_every_type_follows_naming_convention(event_type):
    assert is_valid_name(event_type), f"{event_type!r} violates <domain>.<entity>.<action>"


@pytest.mark.parametrize("event_type", sorted(CATALOG))
def test_every_spec_has_valid_classification(event_type):
    spec = CATALOG[event_type]
    assert spec.type == event_type, "catalog key must equal spec.type"
    assert spec.tier in TIERS
    assert spec.source in SOURCES
    assert spec.default_severity in SEVERITIES
    assert spec.default_outcome in OUTCOMES
    assert spec.status in ("active", "planned")
    assert spec.summary, f"{event_type!r} needs a human summary"


def test_no_duplicate_types():
    # CATALOG is keyed by type, so a dup would collapse; assert the source list length matches.
    from eidolon_data.events import registry

    types = [spec.type for spec in registry._SPECS]
    assert len(types) == len(set(types)), "duplicate event_type in _SPECS"


def test_lookup_helpers():
    assert is_registered("owner.created")
    assert not is_registered("owner.nonexistent")
    assert spec_for("owner.created").source == "data"
    assert spec_for("owner.nonexistent") is None


def test_persona_legacy_events_are_not_registered():
    # Persona v1 deliberately has no old compatibility names: writers emit the
    # observation/proposal/commit/rollback workflow below.
    assert spec_for("persona.genome.created") is None
    assert spec_for("persona_genome.created") is None


def test_active_memory_drift_is_flagged_legacy():
    # Still emitted by the agent->memory fanout path, so it remains explicit.
    assert spec_for("eidolon.memory.fanout.status").legacy is True


def test_activity_tier_only_expected_sources():
    # Activity-tier events are the high-frequency ones; today they only come
    # from hub/channel runtime. Guards against accidentally tiering audit
    # governance events as activity.
    for spec in CATALOG.values():
        if spec.tier == "activity":
            assert spec.source in ("hub", "channel"), f"{spec.type} activity from {spec.source}?"
