"""Transport-neutral audit outbox dispatcher."""

from __future__ import annotations

import logging
from datetime import timedelta

from eidolon_sdk.biz.audit import AuditPublisher
from eidolon_sdk.integrations.audit import should_report_publish_failure

from eidolon_data.audit.outbox import AuditOutboxRepository

logger = logging.getLogger(__name__)


class AuditOutboxDispatcher:
    def __init__(
        self,
        outbox: AuditOutboxRepository,
        publisher: AuditPublisher,
        *,
        batch_size: int = 200,
        retry_base: timedelta = timedelta(seconds=1),
        retry_max: timedelta = timedelta(seconds=60),
    ) -> None:
        self._outbox = outbox
        self._publisher = publisher
        self._batch_size = batch_size
        self._retry_base = retry_base
        self._retry_max = retry_max

    async def dispatch_once(self) -> int:
        batch = await self._outbox.pending_batch(limit=self._batch_size)
        events = batch.events
        if not events:
            return 0
        retry_after = _retry_delay(
            batch.max_attempt_count,
            base=self._retry_base,
            maximum=self._retry_max,
        )
        ids = {event.event_id for event in events}
        try:
            acknowledged = await self._publisher.publish_many(events)
        except Exception as exc:
            # Caught so a bus that is down cannot take this authority with it —
            # but said out loud, which it was not. The outbox column was the only
            # record for 5336 failures over six days, and nothing reads it.
            attempt = batch.max_attempt_count + 1
            if should_report_publish_failure(attempt):
                logger.warning(
                    "audit publish failed (attempt %d, %d event(s) waiting): %s",
                    attempt,
                    len(events),
                    exc,
                )
            await self._outbox.mark_failed(
                ids,
                error=f"{type(exc).__name__}: {exc}",
                retry_after=retry_after,
            )
            return 0
        acknowledged &= ids
        await self._outbox.mark_published(acknowledged)
        missing = ids - acknowledged
        if missing:
            await self._outbox.mark_failed(
                missing,
                error="transport did not acknowledge event",
                retry_after=retry_after,
            )
        return len(acknowledged)


def _retry_delay(
    attempt_count: int,
    *,
    base: timedelta,
    maximum: timedelta,
) -> timedelta:
    exponent = min(max(0, attempt_count), 16)
    seconds = min(base.total_seconds() * (2**exponent), maximum.total_seconds())
    return timedelta(seconds=seconds)
