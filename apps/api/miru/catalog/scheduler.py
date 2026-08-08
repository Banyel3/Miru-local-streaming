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


def scan_now() -> dict:
    """Promote finished downloads and index them.

    Its own function because a completing download must not wait up to thirty
    minutes for the periodic pass. Promotion lives inside scan(), and until this
    existed the ONLY caller was the Scan button in Settings — so a finished
    download sat in `incoming` indefinitely and its card stayed on
    "Adding to your library…" forever.
    """
    from miru.core.db import SessionLocal
    from miru.library.scanner import scan

    with SessionLocal() as db:
        return scan(db)


def _run_once() -> dict:
    """One pass, in a thread. Sync all the way down, like the rest of Miru."""
    from miru.acquisition.prowlarr import provider
    from miru.catalog.ingest import refresh
    from miru.core.db import SessionLocal

    from miru.catalog.enrich import backfill

    # Downloads land on disk between passes, and nothing else was promoting
    # them. Cheap when nothing changed: the scanner skips unchanged files on
    # size and mtime, so a settled library costs one stat per file.
    try:
        scan_now()
    except Exception:  # noqa: BLE001
        log.exception("library scan failed")

    with SessionLocal() as db:
        out = refresh(db, provider)
        # Art is filled after ingest, never during it: a slow or unconfigured
        # metadata provider must not keep releases off the wall.
        try:
            out["enriched"] = backfill(db)
        except Exception:  # noqa: BLE001
            log.exception("enrichment pass failed")
        return out


async def refresh_now() -> dict:
    """Run a pass immediately. Used by the first-run path, which cannot wait
    thirty minutes to show anything."""
    return await asyncio.to_thread(_run_once)


async def _loop(interval: float) -> None:
    while True:
        try:
            # Runs unconditionally. It used to skip whenever the PC was
            # unreachable, because Prowlarr lived there — and that was the
            # coverage hole: each indexer's front page turns over in about ten
            # hours and cannot be paged, so anything uploaded while the PC slept
            # was never seen and never could be. Prowlarr is on the laptop now,
            # and only DOWNLOADS depend on the PC.
            await refresh_now()
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
