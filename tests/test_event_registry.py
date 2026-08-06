from __future__ import annotations

import pytest

from eidolon_data.events import build_event
from eidolon_data.events.registry import spec_for


def test_channel_turn_event_contracts_are_active_and_safe() -> None:
    for event_type in (
        "channel.turn.phase_changed",
        "channel.turn.milestone",
        "channel.turn.completed",
        "channel.turn.rejected",
        "channel.turn.failed",
    ):
        spec = spec_for(event_type)
        assert spec is not None
        assert spec.status == "active"
        assert spec.source == "channel"


def test_channel_phase_requires_ordered_projection_fields() -> None:
    with pytest.raises(ValueError, match="missing required payload"):
        build_event(
            event_type="channel.turn.phase_changed",
            owner_id="owner-1",
            companion_id="companion-1",
            subject_type="turn",
            subject_id="turn-1",
            trace_id="turn-1",
            payload_json={"channel_turn_id": "turn-1", "phase": "user_speech_open"},
        )

    with pytest.raises(ValueError, match="authority-local telemetry"):
        build_event(
            event_type="channel.turn.phase_changed",
            owner_id="owner-1",
            companion_id="companion-1",
            subject_type="turn",
            subject_id="turn-1",
            trace_id="turn-1",
            payload_json={
                "channel_turn_id": "turn-1",
                "phase": "user_speech_open",
                "previous_phase": "idle",
                "transition_seq": 1,
                "side_effect": "none",
                "elapsed_ms": 0,
            },
        )
