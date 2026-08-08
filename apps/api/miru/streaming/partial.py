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
import re
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from miru.acquisition.downloader import downloader, supports_streaming
from miru.streaming import remux
from miru.acquisition.provider import AcquisitionError
from miru.core.config import settings

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
    root. Only the filename is trusted from the downloader; the directory comes
    from our own configuration, so a hostile or confused response cannot point
    this at an arbitrary path.
    """
    incoming = settings.incoming_path
    if not incoming:
        return None

    name = Path(prefix.get("file") or prefix.get("name") or "").name
    if not name:
        return None

    root = Path(incoming).resolve()
    candidate = (root / name).resolve()
    # Containment check, not decoration: `file` comes from another service.
    if root not in candidate.parents and candidate != root:
        log.warning("refusing a live path outside incoming: %s", candidate)
        return None
    if candidate.exists():
        return candidate

    # Multi-file torrents land in a directory named after the torrent.
    folder = (root / Path(prefix.get("name") or "").name).resolve()
    if folder.is_dir() and (root in folder.parents):
        for f in folder.rglob(name):
            return f
    return None


@router.get("/{info_hash}/status")
def live_status(info_hash: str):
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
    }


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
        made = remux.ensure(info_hash_key(info_hash), path, prefix_bytes=ceiling)
        if made == "failed":
            raise HTTPException(
                502, remux.error(info_hash_key(info_hash), ceiling)
                or "Could not make this file playable."
            )
        playable = remux.cached_path(info_hash_key(info_hash), path, ceiling)
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
        # The remux is a whole, finished file: its own length is the ceiling,
        # and there is nothing beyond it to protect the player from.
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

    def body():
        with path.open("rb") as fh:
            fh.seek(start)
            left = length
            while left > 0:
                block = fh.read(min(CHUNK, left))
                if not block:
                    break  # the writer has not caught up; end cleanly
                left -= len(block)
                yield block

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
