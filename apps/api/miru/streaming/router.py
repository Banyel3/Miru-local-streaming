from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from miru.core.db import get_db
from miru.library.models import MediaFile

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

    return FileResponse(
        path,
        media_type=CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream"),
        filename=path.name,
        content_disposition_type="inline",
    )
