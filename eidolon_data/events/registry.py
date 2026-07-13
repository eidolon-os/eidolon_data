"""Event-type registry — the catalog as code.

One authoritative declaration of every event the platform may write to the
``events`` table, with its classification tier, originating subsystem and
default severity/outcome. The ``record_event`` facade (Phase 1) validates
against this catalog so ``event_type`` strings, tiers and sources stop being
implicit conventions scattered across writers.

Naming: ``<domain>.<entity>.<action>`` (lowercase, dot-separated). A few
historical strings drift from that; they are marked ``legacy=True`` while an
active writer still depends on them.

``status``:
  - ``active``  — emitted by code today.
  - ``planned`` — contract declared ahead of the emit point (Phase 2/3),
    so consumers and tests can rely on the shape before it ships.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

Tier = Literal["audit", "activity"]
Source = Literal["data", "agent", "channel", "hub", "memory", "admin"]
Severity = Literal["info", "warn", "error", "critical"]
Outcome = Literal["success", "failure", "denied", "deferred"]
Status = Literal["active", "planned"]

TIERS: tuple[Tier, ...] = ("audit", "activity")
SOURCES: tuple[Source, ...] = ("data", "agent", "channel", "hub", "memory", "admin")
SEVERITIES: tuple[Severity, ...] = ("info", "warn", "error", "critical")
OUTCOMES: tuple[Outcome, ...] = ("success", "failure", "denied", "deferred")

#: ``<domain>.<entity>.<action>`` — at least two lowercase, dot-separated segments.
NAME_RE = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$")


@dataclass(frozen=True)
class EventSpec:
    """The contract for one event type."""

    type: str
    tier: Tier
    source: Source
    summary: str
    default_severity: Severity = "info"
    default_outcome: Outcome = "success"
    required_payload: tuple[str, ...] = ()
    status: Status = "active"
    legacy: bool = False
    note: str = ""


def is_valid_name(event_type: str) -> bool:
    """True when ``event_type`` matches the ``<domain>.<entity>.<action>`` convention."""
    return bool(NAME_RE.match(event_type))


# ── the catalog ──────────────────────────────────────────────────────────
# Grounded on what is emitted today (see docs §1), plus Phase 2/3 contracts
# declared ahead of their emit points (status="planned").
_SPECS: tuple[EventSpec, ...] = (
    # —— owner (governance) ——
    EventSpec("owner.created", "audit", "data", "Owner provisioned"),
    EventSpec("owner.updated", "audit", "admin", "Owner profile updated"),
    EventSpec("owner.archived", "audit", "admin", "Owner archived", default_severity="warn"),
    # —— companion / workspace ——
    EventSpec("companion.created", "audit", "data", "Companion created"),
    EventSpec("companion.workspace.initialized", "audit", "data", "Companion workspace initialized"),
    # —— persona genome lifecycle ——
    EventSpec(
        "persona.observation.created",
        "audit",
        "agent",
        "Persona evolution observation recorded",
        required_payload=("observation_id", "companion_id"),
    ),
    EventSpec(
        "persona.evolution.proposed",
        "audit",
        "agent",
        "Persona genome evolution proposed",
        required_payload=("companion_id",),
    ),
    EventSpec(
        "persona.evolution.approved",
        "audit",
        "admin",
        "Persona evolution proposal approved",
        required_payload=("companion_id",),
    ),
    EventSpec(
        "persona.evolution.rejected",
        "audit",
        "admin",
        "Persona evolution proposal rejected",
        default_severity="warn",
        default_outcome="denied",
    ),
    EventSpec(
        "persona.genome.committed",
        "audit",
        "data",
        "Persona genome committed as current",
        required_payload=("genome_id", "genome_hash"),
    ),
    EventSpec(
        "persona.genome.rolled_back",
        "audit",
        "data",
        "Persona current genome pointer rolled back",
        required_payload=("genome_id", "genome_hash"),
    ),
    # —— memory realm ——
    EventSpec("memory_realm.created", "audit", "data", "Memory realm created"),
    # —— device governance ——
    EventSpec("device.web_body.provisioned", "audit", "data", "Web body device provisioned"),
    EventSpec("device.claimed", "audit", "admin", "Device claimed by owner"),
    EventSpec("device.bound_companion", "audit", "admin", "Device bound to companion"),
    EventSpec("device.approved", "audit", "admin", "Device approved"),
    EventSpec("device.revoked", "audit", "admin", "Device revoked", default_severity="warn"),
    EventSpec("device.released", "audit", "admin", "Device released"),
    EventSpec("device.updated", "audit", "admin", "Device updated"),
    # —— job governance (admin-initiated, active) ——
    EventSpec("job.cancel_requested", "audit", "admin", "Job cancellation requested", default_severity="warn"),
    EventSpec("job.retry_requested", "audit", "admin", "Job retry requested"),
    # —— agent → memory fanout audit (active, legacy name) ——
    EventSpec(
        "eidolon.memory.fanout.status", "audit", "agent", "Agent→memory fanout publish attempt",
        required_payload=("turn_id", "state"),
        legacy=True, note="4-segment legacy name; rename candidate memory.fanout.published",
    ),
    # —— job lifecycle (Phase 2, planned) ——
    EventSpec("job.queued", "audit", "agent", "Long task queued", status="planned"),
    EventSpec("job.running", "audit", "agent", "Long task running", status="planned"),
    EventSpec("job.succeeded", "audit", "agent", "Long task succeeded", status="planned"),
    EventSpec("job.failed", "audit", "agent", "Long task failed", default_severity="error", default_outcome="failure", status="planned"),
    EventSpec("job.timed_out", "audit", "agent", "Long task timed out", default_severity="warn", default_outcome="failure", status="planned"),
    EventSpec("job.cancelled", "audit", "agent", "Long task cancelled", status="planned"),
    # —— memory closure (Phase 2, planned) ——
    EventSpec("memory.fanout.absorbed", "audit", "memory", "Memory runner absorbed the turn", status="planned"),
    EventSpec("memory.fanout.rejected", "audit", "memory", "Memory runner rejected the turn", default_severity="warn", default_outcome="failure", status="planned"),
    EventSpec("memory.fanout.deduped", "audit", "memory", "Memory runner deduped the turn", status="planned"),
    EventSpec("memory.item.created", "audit", "memory", "Memory item created", status="planned"),
    EventSpec("memory.realm.compacted", "audit", "memory", "Memory realm compacted", status="planned"),
    # —— hub runtime (Phase 3, planned) ——
    EventSpec("device.connected", "activity", "hub", "Device came online", status="planned"),
    EventSpec("device.disconnected", "activity", "hub", "Device went offline", status="planned"),
    EventSpec("device.command.dispatched", "activity", "hub", "Body command dispatched", status="planned"),
    EventSpec("device.command.acked", "audit", "hub", "Body command acknowledged", status="planned"),
    EventSpec("device.command.failed", "audit", "hub", "Body command failed", default_severity="warn", default_outcome="failure", status="planned"),
    EventSpec("device.command.denied", "audit", "hub", "Body command denied", default_severity="warn", default_outcome="denied", status="planned"),
    EventSpec("device.proactive.woke", "activity", "hub", "Proactive wake fired", status="planned"),
    # —— channel session (Phase 3, planned) ——
    EventSpec("channel.session.started", "audit", "channel", "Voice session started", status="planned"),
    EventSpec("channel.session.ended", "audit", "channel", "Voice session ended", status="planned"),
    EventSpec("channel.session.failed", "audit", "channel", "Voice session failed", default_severity="error", default_outcome="failure", status="planned"),
    EventSpec("channel.provider.degraded", "audit", "channel", "STT/TTS provider degraded", default_severity="warn", status="planned"),
    # —— agent turn exceptions (Phase 3, planned) ——
    EventSpec("agent.turn.failed", "audit", "agent", "Agent turn failed", default_severity="error", default_outcome="failure", status="planned"),
    # —— ATK Guard control plane (P0) ——
    EventSpec("guard.binding.claimed", "audit", "admin", "Guard binding claimed"),
    EventSpec(
        "guard.binding.configured",
        "audit",
        "admin",
        "Guard binding configuration updated",
        required_payload=("config_revision", "policy_id"),
    ),
    EventSpec(
        "guard.runtime.configured",
        "audit",
        "admin",
        "Guard runtime configuration updated",
        required_payload=("runtime_revision",),
    ),
    EventSpec(
        "guard.runtime.desired_state_changed",
        "audit",
        "admin",
        "Guard runtime desired state changed",
        required_payload=("desired_runtime_state", "runtime_revision"),
    ),
    EventSpec(
        "guard.runtime.applied",
        "audit",
        "hub",
        "Guard runtime desired state applied by device",
        required_payload=("runtime_revision", "desired_runtime_state", "command_id"),
    ),
    EventSpec(
        "guard.runtime.failed",
        "audit",
        "hub",
        "Guard runtime desired state failed on device",
        default_severity="warn",
        default_outcome="failure",
        required_payload=("runtime_revision", "desired_runtime_state", "command_id"),
    ),
    EventSpec("guard.binding.disabled", "audit", "admin", "Guard binding disabled"),
    EventSpec("guard.binding.revoked", "audit", "admin", "Guard binding revoked", default_severity="warn"),
    EventSpec("guard.presence.candidate", "activity", "hub", "Guard presence candidate"),
    EventSpec("guard.presence.verified", "audit", "hub", "Guard presence verified"),
    EventSpec("guard.presence.absent", "activity", "hub", "Guard presence absent"),
    EventSpec("guard.policy.evaluated", "audit", "hub", "Guard policy evaluated"),
    EventSpec("guard.policy.action", "audit", "hub", "Guard policy action published"),
    EventSpec("guard.policy.action_ack", "audit", "hub", "Guard policy action acknowledged"),
)

CATALOG: dict[str, EventSpec] = {spec.type: spec for spec in _SPECS}


def spec_for(event_type: str) -> EventSpec | None:
    """Return the :class:`EventSpec` for ``event_type``, or ``None`` if unregistered."""
    return CATALOG.get(event_type)


def is_registered(event_type: str) -> bool:
    return event_type in CATALOG


def all_types() -> list[str]:
    return sorted(CATALOG)
