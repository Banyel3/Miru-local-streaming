"""Incremental library scan.

`/mnt/` is slow for metadata operations, so this never runs in a request — it
runs in a job. Files unchanged since the last scan (same size and mtime) are
skipped without probing, which is what makes the second scan cheap.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from miru.core.config import settings
from miru.library.incoming import promote
from miru.library.models import Job, MediaFile
from miru.transcode.strategy import probe_file, resolve_strategy
from miru.transcode.subtitles import find_sidecars

log = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".webm", ".ts", ".wmv", ".flv"}


def _walk(roots: list[Path]):
    for root in roots:
        if not root.is_dir():
            log.warning("library path is not a directory: %s", root)
            continue
        for path in root.rglob("*"):
            if path.suffix.lower() in VIDEO_EXTENSIONS and path.is_file():
                yield path


def scan(db: Session, roots: list[Path] | None = None) -> dict:
    roots = roots if roots is not None else settings.libraries

    # Finished downloads join the library first, so a single scan both promotes
    # and indexes them rather than needing two passes.
    moved = {"promoted": 0, "waiting": 0}
    if settings.incoming and roots:
        moved = promote(settings.incoming, roots[0], settings.incoming_settle_seconds)
    existing = {f.path: f for f in db.scalars(select(MediaFile))}
    added = updated = unchanged = 0
    seen: set[str] = set()

    for path in _walk(roots):
        key = str(path)
        seen.add(key)
        stat = path.stat()
        record = existing.get(key)

        if record and record.size_bytes == stat.st_size and record.mtime == stat.st_mtime:
            unchanged += 1
            continue

        probe = probe_file(path)
        record = record or MediaFile(path=key)
        record.title = path.stem
        record.size_bytes = stat.st_size
        record.mtime = stat.st_mtime
        record.duration_ms = probe.duration_ms
        record.container = probe.container
        record.video_codec = probe.video_codec
        record.audio_codec = probe.audio_codec
        record.audio_channels = probe.audio_channels
        record.width = probe.width
        record.height = probe.height
        # Sidecar files sit beside the video and are invisible to ffprobe,
        # but they are how most anime libraries actually ship subtitles.
        record.subtitle_streams = probe.subtitle_streams + find_sidecars(path)
        record.playback_strategy = resolve_strategy(probe)
        record.probed_at = datetime.now(timezone.utc)

        if record.id is None:
            db.add(record)
            added += 1
        else:
            updated += 1

    removed = [f for p, f in existing.items() if p not in seen]
    for record in removed:
        db.delete(record)

    db.commit()
    return {
        "added": added,
        "updated": updated,
        "unchanged": unchanged,
        "removed": len(removed),
        **moved,
    }


def run_scan_job(job_id: int) -> None:
    """Entry point for the background task. Owns its own session because the
    request's session is gone by the time this runs."""
    from miru.core.db import SessionLocal

    with SessionLocal() as db:
        job = db.get(Job, job_id)
        job.status = "running"
        job.attempts += 1
        db.commit()
        try:
            job.payload = scan(db)
            job.status = "done"
        except Exception as exc:  # noqa: BLE001 — the job row is the error report
            log.exception("scan job %s failed", job_id)
            job.status = "failed"
            job.error = str(exc)
        job.finished_at = datetime.now(timezone.utc)
        db.commit()
