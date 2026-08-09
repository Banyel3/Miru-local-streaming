"""Deleting the streams nobody kept.

Watch Now is the Stremio model: the disk is the buffer, and only Keep promotes
a download into the library. Which leaves the other half — a stream that was
watched (or abandoned) and never kept sits in incoming holding gigabytes. This
sweeps them: cancel the torrent WITH its files, forget the job.

The window is a day of idleness, not a day of age: someone partway through a
three-hour film across two evenings has streamed recently, and their buffer
survives. A grab that never streamed a byte (Watch Now pressed, page closed)
ages against when it was grabbed instead, with a wider window.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from miru.acquisition.downloader import downloader
from miru.catalog.models import CatalogWork
from miru.catalog.router import pc_reachable

log = logging.getLogger(__name__)

# Idle this long after last playback → nobody is coming back for it.
IDLE = timedelta(hours=24)
# Never played at all → wait longer before assuming abandonment; a download
# can legitimately take a day on a thin swarm.
UNPLAYED = timedelta(hours=48)


def sweep_ephemeral(db: Session, now: datetime | None = None) -> int:
    """Delete idle ephemeral downloads. Returns how many went.

    The PC being asleep defers rather than forgets: the work keeps its job id
    and the next pass with the PC awake deletes it.
    """
    if not pc_reachable():
        return 0

    now = now or datetime.now(timezone.utc)
    works = db.execute(
        select(CatalogWork).where(
            CatalogWork.ephemeral.is_(True), CatalogWork.download_job_id.isnot(None)
        )
    ).scalars().all()

    gone = 0
    for w in works:
        last = w.last_streamed_at
        if last is not None and last.tzinfo is None:
            last = last.replace(tzinfo=timezone.utc)
        if last is not None:
            idle = now - last >= IDLE
        else:
            seen = w.first_seen_at
            if seen is not None and seen.tzinfo is None:
                seen = seen.replace(tzinfo=timezone.utc)
            idle = seen is not None and now - seen >= UNPLAYED
        if not idle:
            continue

        try:
            downloader().cancel(w.download_job_id, delete_files=True)
        except Exception:  # noqa: BLE001 — the next pass retries
            log.warning("could not delete stream %r; will retry", w.display_title)
            continue
        log.info("swept unkept stream %r", w.display_title)
        w.download_job_id = None
        w.download_name = None
        gone += 1

    if gone:
        db.commit()
    return gone
