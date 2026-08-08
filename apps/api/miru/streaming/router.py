from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from fastapi.responses import RedirectResponse

from miru.core.db import get_db
from miru.library.models import MediaFile
from miru.streaming import remux
from miru.transcode.worker import NEEDS_WORKER, availability, hls_url

router = APIRouter(prefix="/api/stream", tags=["streaming"])

# Browsers sniff loosely, but a wrong type on the direct path can stop
# playback outright, so be explicit for the containers we serve.
CONTENT_TYPES = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".mkv": "video/x-matroska",
}


@router.get("/{file_id}")
def stream(file_id: int, db: Session = Depends(get_db)):
    """Direct play with HTTP Range support.

    Starlette's FileResponse emits Accept-Ranges, 206, and Content-Range
    correctly, so there is nothing to hand-roll here — tests/test_range.py
    holds it to that, because seeking is the whole difference between a media
    server and a download link.
    """
    record = db.get(MediaFile, file_id)
    if not record:
        raise HTTPException(404, "no such file")

    path = Path(record.path)
    if not path.is_file():
        raise HTTPException(410, "file is gone — rescan the library")

    # An MKV is not something a browser can decode, so serve the remuxed copy
    # instead. It is the same video and audio in a container the browser
    # accepts, and it is a real file, so seeking still comes from the same
    # tested Range path rather than from anything new.
    if record.playback_strategy == "remux":
        state = remux.state(file_id, path)
        if state == "ready":
            path = remux.cached_path(file_id, path)
        else:
            remux.ensure(file_id, path)
            raise HTTPException(
                425 if state != "failed" else 500,
                remux.error(file_id) or "Preparing this file for playback — try again shortly.",
            )

    return FileResponse(
        path,
        media_type=CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        filename=path.name,
        content_disposition_type="inline",
    )


@router.get("/{file_id}/index.m3u8")
def hls(file_id: int, db: Session = Depends(get_db)):
    """Redirect to the worker's manifest.

    Kept because the spec lists this endpoint and it is useful for non-browser
    clients, but the web player does NOT use it: it reads `hls_url` off the file
    payload and fetches the worker directly. A browser following a cross-origin
    CORS redirect sends `Origin: null` on the second hop, which the worker cannot
    match against any allowlist, so the manifest fetch fails regardless of CORS
    configuration. The redirect is fine for curl and for players that do not
    enforce CORS.
    """
    record = db.get(MediaFile, file_id)
    if not record:
        raise HTTPException(404, "no such file")

    if record.playback_strategy not in NEEDS_WORKER:
        raise HTTPException(
            409, f"{record.playback_strategy} does not need transcoding — use /api/stream/{file_id}"
        )

    state, note = availability(record.playback_strategy)
    if state != "gpu-ready":
        raise HTTPException(503, note or "transcode worker unavailable")

    return RedirectResponse(
        hls_url(file_id, record.playback_strategy, record.height), status_code=302
    )
