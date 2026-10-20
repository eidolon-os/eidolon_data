"""Authority-owned expiry; no recording or model dependencies."""

import asyncio
import logging


async def expire_voiceprint_enrollments(voiceprints):
    while True:
        try:
            await voiceprints.expire_enrollments()
        except Exception:
            logging.getLogger(__name__).exception("voiceprint enrollment expiry failed")
        await asyncio.sleep(60)
