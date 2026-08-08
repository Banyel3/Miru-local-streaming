"""The periodic refresh.

One asyncio task started in the app lifespan, not APScheduler. The house
position is already recorded in `library/router.py:85` — BackgroundTasks rather
than a scheduler — and adding a scheduling dependency for a single job would
reverse a deliberate call to buy nothing.
"""

from __future__ import annotations

import asyncio
import logging

from miru.core.config import settings

log = logging.getLogger(__name__)

_task: asyncio.Task | None = None


def _run_once() -> dict:
    """One pass, in a thread. Sync all the way down, like the rest of Miru."""
    from miru.acquisition.prowlarr import provider
    from miru.catalog.ingest import refresh
    from miru.core.db import SessionLocal

    with SessionLocal() as db:
        return refresh(db, provider)


async def refresh_now() -> dict:
    """Run a pass immediately. Used by the first-run path, which cannot wait
    thirty minutes to show anything."""
    return await asyncio.to_thread(_run_once)


async def _loop(interval: float) -> None:
    while True:
        try:
            # Skip entirely when the PC is unreachable. Prowlarr lives there, so
            # every request would fail, and a log full of connection errors is
            # exactly how a real failure gets missed.
            from miru.catalog.router import pc_reachable

            if pc_reachable():
                await refresh_now()
            else:
                log.debug("catalog refresh skipped: PC unreachable")
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — a bad pass must not kill the loop
            log.exception("catalog refresh raised")
        await asyncio.sleep(interval)


def start() -> None:
    global _task
    if _task is not None or not settings.catalog_refresh_seconds:
        return
    _task = asyncio.create_task(_loop(settings.catalog_refresh_seconds))
    log.info("catalog refresh every %ss", settings.catalog_refresh_seconds)


async def stop() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except (asyncio.CancelledError, Exception):  # noqa: BLE001
        pass
    _task = None
