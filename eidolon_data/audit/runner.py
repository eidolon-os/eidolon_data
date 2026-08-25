"""The loop that gives the outbox dispatcher a transport, and a retention.

``AuditOutboxDispatcher`` has existed and been tested since the outbox did, and
it is transport-neutral on purpose. What was missing is this: something that
hands it a real publisher, runs it, and decides when a published row may be
deleted. Until now nothing did — so this authority's governance facts never
reached the global stream, and the outbox grew without bound.

**The retention is a product decision, not a transport one.** The same rows are
what ``GET /owners/{id}/governance-events`` answers with, which is the 主机动态 a
person reads; nothing else on this Host keeps that history. The Agent's
dispatcher keeps published rows for a day and is right to — nothing reads its
outbox for a person. Ours is somebody's history.

**A Host with no bus keeps everything.** With no URL configured nothing runs: no
publishing, and no purging. That is the safe direction, because an unpublished
row is still readable by its Owner and a purged one is gone.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime, timedelta

from eidolon_sdk.integrations.audit import (
    AuditNatsPublisherSettings,
    JetStreamAuditPublisher,
)

from eidolon_data.audit.dispatcher import AuditOutboxDispatcher
from eidolon_data.audit.outbox import AuditOutboxRepository

logger = logging.getLogger(__name__)

#: How long a published governance fact stays readable to its Owner.
#:
#: Ninety days: long enough for a person to find the week they are thinking of,
#: short enough that a Host is not keeping every fact of its life in the table
#: its writers contend on. Raising it is safe; lowering it silently shortens
#: somebody's history, which is worth saying out loud in the same breath.
OWNER_HISTORY_RETENTION = timedelta(days=90)

_PURGE_INTERVAL_SECONDS = 60 * 60
_IDLE_SLEEP_SECONDS = 0.5


async def purge_expired_audit(
    outbox: AuditOutboxRepository,
    *,
    now: datetime | None = None,
    retention: timedelta = OWNER_HISTORY_RETENTION,
) -> int:
    """Delete published rows older than a person is expected to look back.

    Published ones only — the repository enforces that, and it is the property
    that makes a bus outage cost delay rather than history.
    """

    moment = now or datetime.now(UTC)
    return await outbox.purge_published(before=moment - retention)


async def run_audit_dispatcher(
    outbox: AuditOutboxRepository,
    *,
    nats_url: str,
    retention: timedelta = OWNER_HISTORY_RETENTION,
) -> None:
    """Drain this authority's outbox forever; no sibling database is opened."""

    publisher = JetStreamAuditPublisher(
        AuditNatsPublisherSettings(url=nats_url),
        connection_name="eidolon-data-audit-publisher",
    )
    dispatcher = AuditOutboxDispatcher(outbox, publisher)
    next_purge = 0.0
    try:
        while True:
            published = 0
            try:
                published = await dispatcher.dispatch_once()
                now = time.monotonic()
                if now >= next_purge:
                    await purge_expired_audit(outbox, retention=retention)
                    next_purge = now + _PURGE_INTERVAL_SECONDS
            except asyncio.CancelledError:
                raise
            except Exception:
                # A bus that is down must not take an authority with it, and the
                # rows it could not publish are rows its Owner can still read.
                logger.exception("audit dispatcher iteration failed")
            if published == 0:
                await asyncio.sleep(_IDLE_SLEEP_SECONDS)
    finally:
        await publisher.close()


__all__ = [
    "OWNER_HISTORY_RETENTION",
    "purge_expired_audit",
    "run_audit_dispatcher",
]
