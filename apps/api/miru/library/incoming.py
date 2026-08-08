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


def holds_video(entry: Path) -> bool:
    """Whether this entry is, or contains, something the library can play.

    A torrent contains whatever its maker put in it, and the categories that
    reach the downloader are only as good as the filter in front of them. A
    `PC/Games` grab used to land as a directory of executables inside the media
    library — where the scanner ignored it, so nothing in Miru ever said it was
    there.

    Reuses the scanner's own list rather than a second copy of it: two lists of
    video extensions drift, and the one that decides what is playable is the
    one that should decide what gets moved.
    """
    from miru.library.scanner import VIDEO_EXTENSIONS

    if entry.is_file():
        return entry.suffix.lower() in VIDEO_EXTENSIONS
    return any(p.suffix.lower() in VIDEO_EXTENSIONS for p in entry.rglob("*") if p.is_file())


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


def link_catalog_work(db, path, file_id: int) -> None:
    """Point the catalog card at the file it became.

    Without this the card never leaves "Adding to your library…", the ready
    toast never fires, and the wall keeps offering to download something you
    already own. The column existed and nothing ever wrote it.

    Matched on the download's own name rather than on a parsed title, because
    the downloader named the file and that is the one string both sides agree
    on.
    """
    try:
        from miru.catalog.models import CatalogRelease, CatalogWork

        stem = path.name
        rel = (
            db.query(CatalogRelease)
            .filter(CatalogRelease.title.ilike(f"%{stem.rsplit('.', 1)[0][:60]}%"))
            .first()
        )
        if rel is None or rel.work_id is None:
            return
        work = db.get(CatalogWork, rel.work_id)
        if work is not None and work.library_file_id is None:
            work.library_file_id = file_id
            db.commit()
    except Exception:  # noqa: BLE001 - a failed link must not fail the scan
        log.warning("could not link %s to a catalog work", path.name, exc_info=True)


def promote(incoming: Path, library: Path, settle_seconds: float = 120.0) -> dict:
    """Move every settled entry from incoming into the library.

    Returns counts for the job payload. Never raises for one bad entry — a
    single undeletable file should not stop the rest of a scan.
    """
    if not incoming.is_dir() or not library.is_dir():
        return {"promoted": 0, "waiting": 0, "skipped": 0, "names": []}

    promoted = waiting = skipped = 0
    # Reported so the scan can point each catalog card at the file it became.
    names: list[str] = []
    for entry in sorted(incoming.iterdir()):
        if entry.name.startswith("."):
            continue

        if not is_settled(entry, settle_seconds):
            waiting += 1
            continue

        if not holds_video(entry):
            # Left where it is rather than deleted. Nothing here is certain
            # enough to destroy a download on — and unlike `waiting`, this is
            # not a thing to come back to: an installer never becomes a film,
            # so counting it as waiting would report work that never ends.
            log.warning("not promoting %s — it holds no video", entry.name)
            skipped += 1
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
            names.append(entry.name)
        except OSError as exc:
            log.warning("could not promote %s: %s", entry.name, exc)
            waiting += 1

    return {"promoted": promoted, "waiting": waiting, "skipped": skipped, "names": names}
