"""Watching a file that is still arriving.

No new subsystem: Miru already serves files from disk over HTTP Range, and a
sequentially-filled file is readable from the front while it is still growing.
The only thing that has to be added is a ceiling — reads are capped at the
contiguous downloaded prefix, so the player can never seek past what exists.

That ceiling is the whole correctness argument. Percent-complete says nothing
about *which* percent: with normal piece order the first byte may be missing
while the last is present, and serving a hole returns zeros that decode as
corruption rather than as an error anyone can see. The prefix is computed from
qBittorrent's per-piece state, so it is a fact rather than an estimate.

Not available with aria2, which has no sequential download for BitTorrent, so a
prefix would be meaningless there.
"""

from __future__ import annotations

import hashlib
import logging
import time
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from fastapi.responses import StreamingResponse

from miru.acquisition.downloader import downloader, supports_streaming
from miru.streaming import remux
from miru.acquisition.provider import AcquisitionError
from miru.catalog.models import CatalogWork
from miru.core.config import settings
from miru.core.db import get_db

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/stream/live", tags=["streaming"])

CHUNK = 256 * 1024

# Enough of the front to start decoding rather than stalling on the first frame.
# Playback that begins and immediately freezes is worse than playback that
# starts twenty seconds later, which is the failure this design has refused
# twice already.
MIN_PLAYABLE_BYTES = 24 * 1024 * 1024

_RANGE = re.compile(r"bytes=(\d*)-(\d*)")

# What a browser will actually decode from a <video> src. Matroska is not on the
# list however good the codecs inside it are, and neither is AVI — the container
# decides this, not the codec.
_BROWSER_CONTAINERS = {".mp4", ".m4v", ".webm"}


def info_hash_key(info_hash: str) -> int:
    """A stable integer id for the remux cache, which is keyed by file id.

    A live download has no library row yet, so there is no file id to use. The
    infohash is the identity everything else in this path already uses.

    Hashed rather than read as hex: this value comes out of the URL path, so it
    is whatever the caller typed, and `int(x, 16)` on that is a ValueError —
    which inside a route is a 500 with a stack trace instead of an answer.
    """
    return int.from_bytes(hashlib.sha1(info_hash.encode()).digest()[:6], "big")


_touched: dict[str, float] = {}


def _touch_last_streamed(info_hash: str) -> None:
    now = time.monotonic()
    if now - _touched.get(info_hash, 0.0) < 60:
        return
    _touched[info_hash] = now
    try:
        from datetime import datetime, timezone

        from miru.catalog.models import CatalogWork
        from miru.core.db import SessionLocal
        from sqlalchemy import update

        db = SessionLocal()
        try:
            db.execute(
                update(CatalogWork)
                .where(CatalogWork.download_job_id == info_hash)
                .values(last_streamed_at=datetime.now(timezone.utc))
            )
            db.commit()
        finally:
            db.close()
    except Exception:  # noqa: BLE001 — a failed touch must not fail the stream
        log.debug("could not touch last_streamed_at for %s", info_hash)


def _read_exactly(
    path: Path,
    *,
    start: int,
    length: int,
    retry_wait: float = 0.2,
    retries: int = 5,
    on_wait=None,
    chunk: int | None = None,
):
    """Exactly `length` bytes from `start`, or a loud failure — never fewer.

    Content-Length went on the wire before this runs, and a generator that
    "ends cleanly" short of it delivers a truncated response the client sees as
    a mid-playback network error with nothing to explain it. Bytes below the
    stat'd ceiling are append-only in every path served here, so an empty read
    inside the promised range means the source was replaced or truncated —
    after a brief retry for a writer mid-flush, aborting is the honest answer.

    `on_wait` is a test seam, called before each retry sleep.

    Unbuffered on purpose: this loop does its own chunking, so BufferedReader
    only adds a copy — and its read-ahead serves bytes from memory after the
    file has been truncated underneath it, hiding exactly the condition this
    exists to catch.
    """
    with path.open("rb", buffering=0) as fh:
        fh.seek(start)
        left = length
        patience = retries
        while left > 0:
            block = fh.read(min(chunk or CHUNK, left))
            if not block:
                if patience > 0:
                    patience -= 1
                    if on_wait:
                        on_wait()
                    time.sleep(retry_wait)
                    continue
                raise RuntimeError(
                    f"{path.name}: source ended {left} bytes short of the "
                    "promised range — replaced or truncated underneath the reader"
                )
            patience = retries
            left -= len(block)
            yield block


def _ceiling_now(info_hash: str, path: Path) -> int:
    """How much of the source is readable, asked again as the download runs."""
    try:
        p = downloader().playable_prefix(info_hash)
    except AcquisitionError:
        return 0
    on_disk = path.stat().st_size if path.exists() else 0
    if p.get("complete"):
        return on_disk
    return max(0, min(int(p.get("playable_bytes") or 0), on_disk))


def _still_downloading(info_hash: str) -> bool:
    """Whether there is any point waiting for more bytes.

    A cancelled or finished torrent must end the follow, or its ffmpeg lives on
    with nothing to read.
    """
    try:
        p = downloader().playable_prefix(info_hash)
    except AcquisitionError:
        return False
    return not p.get("complete")


def needs_remux(path: Path) -> bool:
    """Whether handing this file to a browser would simply fail.

    The live stream used to serve the growing file exactly as it sat on disk and
    label it `video/x-matroska`, which is honest and useless: no browser plays
    Matroska, so every MKV download — most anime — was unwatchable for the whole
    time it was downloading. The label being truthful only made the failure
    silent, because the player was handed a container it refuses and had nothing
    to say about it.
    """
    return path.suffix.lower() not in _BROWSER_CONTAINERS


def _local_path(prefix: dict) -> Path | None:
    """Where the growing file is on *this* machine.

    The downloader runs on the PC and writes into the laptop's incoming
    directory over NFS, so the same bytes are readable here under a different
    root. The DOWNLOADER's answer is used, not a guess from the torrent name:
    a torrent named `Scary Movie 2026…` unpacked into a folder named
    `www.UIndex.org    -    Scary Movie 2026…`, and guessing the folder from
    the name left a 100%-downloaded film "waiting for the first pieces to
    land" forever. `file` is the path relative to the save dir; `content_path`
    names the real top-level entry. Both are tried, then the old flat lookup.

    Only relative paths are trusted from the downloader; the root comes from
    our own configuration, so a hostile or confused response cannot point this
    at an arbitrary path.
    """
    incoming = settings.incoming_path
    if not incoming:
        return None
    root = Path(incoming).resolve()

    def contained(p: Path) -> Path | None:
        r = p.resolve()
        return r if (root in r.parents or r == root) and r.exists() and r.is_file() else None

    # 1. The relative path exactly as the downloader states it.
    rel = (prefix.get("file") or "").lstrip("/")
    if rel:
        if got := contained(root / rel):
            return got

    # 2. The real on-disk entry, from content_path's basename — the torrent
    #    name and the folder name are different strings.
    content = Path(prefix.get("content_path") or "").name
    fname = Path(rel or prefix.get("name") or "").name
    if content:
        entry = root / content
        if got := contained(entry):
            return got
        if entry.resolve().is_dir() and root in entry.resolve().parents and fname:
            for f in entry.rglob(fname):
                if got := contained(f):
                    return got

    # 3. The old flat lookups, still valid for single-file torrents.
    if fname:
        if got := contained(root / fname):
            return got
    folder = root / Path(prefix.get("name") or "").name
    if folder.resolve().is_dir() and root in folder.resolve().parents and fname:
        for f in folder.rglob(fname):
            if got := contained(f):
                return got
    return None


@router.get("/{info_hash}/status")
def live_status(info_hash: str, db: Session = Depends(get_db)):
    """Whether this can be watched yet, and how much of it."""
    if not supports_streaming():
        raise HTTPException(
            409,
            "The configured downloader can't be watched while downloading. "
            "Playback starts when the file finishes.",
        )
    try:
        prefix = downloader().playable_prefix(info_hash)
    except AcquisitionError as exc:
        raise HTTPException(502, str(exc)) from exc

    path = _local_path(prefix)
    ready = prefix["playable_bytes"] if path else 0
    return {
        **prefix,
        "found_on_disk": path is not None,
        # Enough to start, rather than merely non-zero.
        "watchable": bool(path) and (prefix["complete"] or ready >= MIN_PLAYABLE_BYTES),
        "min_bytes": MIN_PLAYABLE_BYTES,
        # Where this went once it finished. The mover takes a completed download
        # out of incoming, so the live stream stops having it and the page 404s
        # — at the exact moment the film became fully watchable. This is what the
        # page follows instead of showing an error.
        "library_file_id": _promoted_to(db, info_hash),
        # Whether this stream is being kept. The player's Keep banner reads it
        # so a kept download does not offer Keep again after a reload.
        "ephemeral": _is_ephemeral(db, info_hash),
    }


def _is_ephemeral(db: Session, info_hash: str) -> bool:
    return bool(
        db.execute(
            select(CatalogWork.ephemeral).where(CatalogWork.download_job_id == info_hash)
        ).scalar_one_or_none()
    )


def _promoted_to(db: Session, info_hash: str) -> int | None:
    """The library file this download became, once the scan has linked it."""
    return db.execute(
        select(CatalogWork.library_file_id).where(
            CatalogWork.download_job_id == info_hash
        )
    ).scalar_one_or_none()


@router.get("/{info_hash}")
def live_stream(info_hash: str, request: Request, range_header: str | None = Header(None, alias="Range")):
    """Serve the completed prefix of a file that is still downloading."""
    if not supports_streaming():
        raise HTTPException(409, "This downloader can't be streamed from.")

    try:
        prefix = downloader().playable_prefix(info_hash)
    except AcquisitionError as exc:
        raise HTTPException(502, str(exc)) from exc

    path = _local_path(prefix)
    if path is None:
        raise HTTPException(404, "That file hasn't appeared on disk yet.")

    total = prefix["size_bytes"] or path.stat().st_size
    # Never trust the reported prefix past what is actually on disk: the NFS
    # write and the piece-state report are two different machines' opinions,
    # and the smaller one is the safe one.
    on_disk = path.stat().st_size
    ceiling = total if prefix["complete"] else max(0, min(prefix["playable_bytes"], on_disk))

    if ceiling <= 0:
        raise HTTPException(416, "Nothing is playable yet.")

    if needs_remux(path):
        # Remuxed to a fragmented MP4 rather than served as it lies. The bytes
        # are Matroska and no browser decodes that, so serving them was a
        # guaranteed failure with an honest label on it.
        #
        # Keyed on the ceiling, so each time the player runs out and asks again
        # — see lib/live.ts and the `?resume=` it appends — it gets a longer
        # copy rather than the same short one for the rest of the download.
        # Followed rather than remade. `prefix_bytes` is a callable so the one
        # ffmpeg started here keeps reading as pieces land, and `alive` is what
        # ends it — without that a cancelled download holds a process forever.
        key = info_hash_key(info_hash)
        made = remux.ensure(
            key,
            path,
            prefix_bytes=lambda: _ceiling_now(info_hash, path),
            alive=lambda: _still_downloading(info_hash),
        )
        if made == "failed":
            raise HTTPException(
                502, remux.error(key) or "Could not make this file playable."
            )
        playable = remux.cached_path(key, path)
        if made != "ready" or not playable.exists():
            # "Not yet", not "never". The buffering overlay already knows how to
            # sit through a wait; zeros or a refused container would read to the
            # user as a corrupt download.
            raise HTTPException(
                503,
                "Making this playable in your browser — it will start shortly.",
                headers={"Retry-After": "5"},
            )
        path = playable
        # The remux's own size, not the source's completed prefix. It is a
        # different length — subtitles are dropped and the container changes —
        # and it is still being written, so promising the source's ceiling would
        # promise bytes this file does not have and the read would come up short
        # under a Content-Length already sent.
        total = ceiling = path.stat().st_size

    start, end = 0, ceiling - 1
    if range_header:
        # fullmatch, not match: "bytes=0-10,20-30" used to be accepted and then
        # answered with only the first range, and "bytes=100-50" produced a
        # negative Content-Length on the wire.
        m = _RANGE.fullmatch(range_header.strip())
        if not m:
            # RFC 9110 §14.2: an unsatisfiable *syntax* is ignored, not 416'd.
            m = None
    if range_header and m:
        raw_start, raw_end = m.group(1), m.group(2)
        if raw_start:
            start = int(raw_start)
            # Checked against the RAW end, before clamping. A request whose end
            # is below its start is malformed and is ignored; one that merely
            # reaches past the ceiling is unsatisfiable and must still 416
            # below, which is a different answer.
            if raw_end and int(raw_end) < start:
                start, end = 0, ceiling - 1
            elif raw_end:
                end = min(int(raw_end), ceiling - 1)
            else:
                end = ceiling - 1
        elif raw_end:  # suffix range, how a player finds a trailing index
            length = int(raw_end)
            start = max(0, ceiling - length)
            end = ceiling - 1

    if start >= ceiling:
        # Seeking past what has arrived. 416 with the *current* ceiling tells
        # the player the truth rather than handing it zeros.
        raise HTTPException(
            416,
            "That part hasn't downloaded yet.",
            headers={"Content-Range": f"bytes */{total}"},
        )

    length = end - start + 1
    body = lambda: _read_exactly(path, start=start, length=length)  # noqa: E731

    # The janitor ages ephemeral streams against this. Throttled: a player
    # fetches many ranges a minute and one UPDATE per range is a write per
    # seek for a value nobody reads at second granularity.
    _touch_last_streamed(info_hash)

    return StreamingResponse(
        body(),
        status_code=206,
        media_type="video/webm" if path.suffix.lower() == ".webm" else "video/mp4",
        headers={
            "Content-Range": f"bytes {start}-{end}/{total}",
            "Content-Length": str(length),
            "Accept-Ranges": "bytes",
            # The ceiling moves as pieces land, so nothing here may be cached.
            "Cache-Control": "no-store",
            "X-Miru-Playable-Bytes": str(ceiling),
        },
    )
