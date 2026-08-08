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

import logging
import re
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from miru.acquisition.downloader import downloader, supports_streaming
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

    start, end = 0, ceiling - 1
    if range_header:
        m = _RANGE.match(range_header)
        if not m:
            raise HTTPException(416, "Bad range")
        raw_start, raw_end = m.group(1), m.group(2)
        if raw_start:
            start = int(raw_start)
            if raw_end:
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
        media_type="video/mp4" if path.suffix.lower() in {".mp4", ".m4v"} else "video/x-matroska",
        headers={
            "Content-Range": f"bytes {start}-{end}/{total}",
            "Content-Length": str(length),
            "Accept-Ranges": "bytes",
            # The ceiling moves as pieces land, so nothing here may be cached.
            "Cache-Control": "no-store",
            "X-Miru-Playable-Bytes": str(ceiling),
        },
    )
