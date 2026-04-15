"""
Background scheduler — runs auto_sync periodically.
Interval configurable via SYNC_INTERVAL_HOURS env var (default: 6h).
"""

import asyncio
import logging
import os
from services.auto_sync import run_full_sync

logger = logging.getLogger(__name__)

SYNC_INTERVAL_HOURS = int(os.getenv("SYNC_INTERVAL_HOURS", "6"))


async def scheduler_loop():
    logger.info(f"[scheduler] Starting — sync every {SYNC_INTERVAL_HOURS}h")
    while True:
        try:
            logger.info("[scheduler] Running auto-sync...")
            stats = await run_full_sync(auto_publish=True)
            logger.info(f"[scheduler] Sync complete: {stats}")
        except Exception as e:
            logger.error(f"[scheduler] Sync error: {e}")
        await asyncio.sleep(SYNC_INTERVAL_HOURS * 3600)
