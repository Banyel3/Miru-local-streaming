"""Promote finished downloads from the incoming directory into the library.

The scanner deliberately does not watch the incoming directory. A download in
progress is a file that grows, and probing one records a permanently wrong
answer: ffprobe reads a truncated header, the strategy resolves from garbage,
and because the scan also writes size and mtime the file is never re-probed.
So downloads land somewhere unscanned and are moved in only once finished.

"Finished" is decided by stillness — nothing in the entry has been written to
for a while. That is a heuristic, not a signal, and it is the honest limit of
what can be known without the downloader telling us.
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

log = logging.getLogger(__name__)

# Extensions a downloader uses for a file it is still writing. Present means
# in-progress no matter how still the directory looks.
PARTIAL_SUFFIXES = {".part", ".!qb", ".crdownload", ".aria2", ".tmp", ".downloading"}


def _newest_mtime(entry: Path) -> float:
    """Most recent write anywhere inside the entry."""
    if entry.is_file():
        return entry.stat().st_mtime
    times = [p.stat().st_mtime for p in entry.rglob("*") if p.is_file()]
    return max(times, default=entry.stat().st_mtime)


def _has_partials(entry: Path) -> bool:
    if entry.is_file():
        return entry.suffix.lower() in PARTIAL_SUFFIXES
    return any(p.suffix.lower() in PARTIAL_SUFFIXES for p in entry.rglob("*"))


def is_settled(entry: Path, settle_seconds: float, now: float | None = None) -> bool:
    """Has this entry been untouched long enough to call it finished?

    ponytail: stillness is a heuristic. A stalled torrent looks exactly like a
    finished one, so a stalled download will be promoted and then probe as
    broken. The real fix is a completion callback from the downloader, which is
    M6 work; until then the settle window is the knob.
    """
    if _has_partials(entry):
        return False
    try:
        return (now or time.time()) - _newest_mtime(entry) >= settle_seconds
    except OSError:
        return False


def promote(incoming: Path, library: Path, settle_seconds: float = 120.0) -> dict:
    """Move every settled entry from incoming into the library.

    Returns counts for the job payload. Never raises for one bad entry — a
    single undeletable file should not stop the rest of a scan.
    """
    if not incoming.is_dir() or not library.is_dir():
        return {"promoted": 0, "waiting": 0}

    promoted = waiting = 0
    for entry in sorted(incoming.iterdir()):
        if entry.name.startswith("."):
            continue

        if not is_settled(entry, settle_seconds):
            waiting += 1
            continue

        target = library / entry.name
        if target.exists():
            # Same name already in the library. Leave it alone rather than
            # overwrite something that might be the copy actually being watched.
            log.warning("not promoting %s — %s already exists", entry.name, target)
            waiting += 1
            continue

        try:
            # Same filesystem is an atomic rename; shutil falls back to a copy
            # across devices, which is slower but still correct.
            shutil.move(str(entry), str(target))
            log.info("promoted %s into the library", entry.name)
            promoted += 1
        except OSError as exc:
            log.warning("could not promote %s: %s", entry.name, exc)
            waiting += 1

    return {"promoted": promoted, "waiting": waiting}
