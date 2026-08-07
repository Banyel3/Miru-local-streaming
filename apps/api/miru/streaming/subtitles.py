from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from miru.core.config import settings
from miru.core.db import get_db
from miru.library.models import MediaFile
from miru.transcode.subtitles import (
    SubtitleError,
    convert_sidecar,
    extract_embedded,
    format_for,
)

router = APIRouter(prefix="/api/subtitles", tags=["subtitles"])

MEDIA_TYPES = {
    "ass": "text/x-ssa; charset=utf-8",
    "vtt": "text/vtt; charset=utf-8",
}


def _track_at(file: MediaFile, track: int) -> dict:
    """Tracks are addressed by position in the list the API published, so the
    row the UI rendered is the row that gets served. Embedded stream indices
    are not contiguous and external files have none at all, which is why the
    position — not the ffprobe index — is the identifier."""
    tracks = file.subtitle_streams or []
    if track < 0 or track >= len(tracks):
        raise HTTPException(404, "no such subtitle track")
    return tracks[track]


@router.get("/{file_id}/{track}")
def subtitle(file_id: int, track: int, format: str | None = None, db: Session = Depends(get_db)):
    """Serve one subtitle track as ASS (styling preserved) or WebVTT.

    Extraction never touches the video stream — spec §7. The result is cached
    on disk because it costs an ffmpeg process and a read across a slow mount,
    and it cannot change unless the source file does.

    `?format=vtt` asks for the WebVTT form of an ASS track. The player uses it
    to populate the native captions menu and as the fallback if JASSUB fails to
    load; the styled ASS is fetched separately and drawn over the top. Styling,
    positioning and karaoke are lost in the VTT form — that is the trade, and
    it only applies to the fallback.
    """
    record = db.get(MediaFile, file_id)
    if not record:
        raise HTTPException(404, "no such file")

    meta = _track_at(record, track)
    fmt = format_for(meta.get("codec"))
    if format == "vtt":
        fmt = "vtt"
    elif format not in (None, "ass", "vtt"):
        raise HTTPException(400, "format must be 'ass' or 'vtt'")

    cached = settings.cache / "subtitles" / f"{file_id}-{track}.{fmt}"

    if not cached.exists():
        try:
            if meta.get("source") == "external":
                source = Path(meta["path"])
                if not source.is_file():
                    raise HTTPException(410, "subtitle file is gone — rescan the library")
                convert_sidecar(source, cached)
            else:
                video = Path(record.path)
                if not video.is_file():
                    raise HTTPException(410, "media file is gone — rescan the library")
                extract_embedded(video, int(meta["index"]), cached)
        except SubtitleError as exc:
            raise HTTPException(422, f"could not extract subtitles: {exc}") from exc

    return FileResponse(
        cached,
        media_type=MEDIA_TYPES[fmt],
        headers={"Cache-Control": "public, max-age=86400"},
    )
