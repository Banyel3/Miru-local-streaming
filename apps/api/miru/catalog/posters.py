"""Serving poster art.

Images are proxied and cached on disk rather than linked directly, for three
reasons that all matter here: the browser makes no third-party requests, so the
wall keeps working when TMDB is slow or blocked; posters are fetched once
instead of on every render; and the URL the client asks for is a work id rather
than a remote address, so nothing the metadata provider returns is ever
reachable from the browser.

A miss is a 404, deliberately. That is what lets the client fall back to
`ArtTile` — a placeholder image, or a 200 with an empty body, would leave it
rendering a broken frame with no way to tell.
"""

from __future__ import annotations

import hashlib
import logging
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, Response
from sqlalchemy.orm import Session

from miru.catalog.enrich import ALLOWED_IMAGE_HOSTS
from miru.catalog.models import CatalogWork
from miru.core.config import settings
from miru.core.db import get_db

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/posters", tags=["catalog"])

MAX_BYTES = 8 * 1024 * 1024


def _cache_dir() -> Path:
    root = Path(settings.poster_cache_path or "/tmp/miru-posters")
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cache_path(url: str) -> Path:
    # Hashed, because these URLs carry query strings and path separators and
    # are not safe as filenames. The extension is kept so the media type can be
    # derived without re-reading the file.
    ext = Path(urllib.parse.urlparse(url).path).suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        ext = ".jpg"
    return _cache_dir() / f"{hashlib.sha256(url.encode()).hexdigest()[:32]}{ext}"


def _allowed(url: str) -> bool:
    """Whether this is a host we are willing to fetch from.

    The URL comes out of a third-party API response, so without this the
    provider chooses what our server requests. Same class of problem as the
    worker's source allowlist, and not hypothetical.
    """
    try:
        parsed = urllib.parse.urlparse(url)
    except ValueError:
        return False
    return parsed.scheme == "https" and parsed.hostname in ALLOWED_IMAGE_HOSTS


def _fetch(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Miru/0.1"})
        with urllib.request.urlopen(req, timeout=20) as res:
            if res.status != 200:
                return False
            data = res.read(MAX_BYTES + 1)
    except (urllib.error.URLError, OSError) as exc:
        log.info("poster fetch failed for %s: %s", url, exc)
        return False

    if not data or len(data) > MAX_BYTES:
        return False

    # Written via a temporary name so a half-downloaded poster is never served
    # as a complete one.
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(data)
    tmp.replace(dest)
    return True


@router.get("/{work_id}")
def poster(work_id: int, db: Session = Depends(get_db)):
    work = db.get(CatalogWork, work_id)
    if work is None or not work.poster_url:
        raise HTTPException(404, "no poster")

    url = work.poster_url
    if not _allowed(url):
        log.warning("refusing to fetch poster from %s", urllib.parse.urlparse(url).hostname)
        raise HTTPException(404, "no poster")

    try:
        path = _cache_path(url)
        if not path.exists() and not _fetch(url, path):
            raise HTTPException(404, "no poster")
    except OSError as exc:
        # The cache lives on the storage disk, and that disk can drop off USB
        # — it did, and every poster on the site became a 500. A cache being
        # unavailable is a miss, not a server error: 404 leaves the tint tiles
        # up and the site usable while the mount is dead.
        log.warning("poster cache unavailable (%s); serving misses", exc)
        raise HTTPException(404, "no poster") from exc

    media = {
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(path.suffix, "image/jpeg")

    return FileResponse(
        path,
        media_type=media,
        headers={
            # Posters do not change. A year is not optimism, it is what the
            # cache is for.
            "Cache-Control": "public, max-age=31536000, immutable",
        },
    )


@router.head("/{work_id}")
def poster_head(work_id: int, db: Session = Depends(get_db)):
    work = db.get(CatalogWork, work_id)
    if work is None or not work.poster_url or not _allowed(work.poster_url):
        raise HTTPException(404, "no poster")
    return Response(status_code=200)
