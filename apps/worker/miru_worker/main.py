import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse

from miru_worker import sessions
from miru_worker.config import settings
from miru_worker.ladder import is_allowed, session_id

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

MEDIA_TYPES = {
    ".m3u8": "application/vnd.apple.mpegurl",
    ".ts": "video/mp2t",
    ".m4s": "video/iso.segment",
    ".mp4": "video/mp4",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.cache.mkdir(parents=True, exist_ok=True)
    log.info("worker ready — encoder=%s cache=%s", sessions.ENCODER, settings.cache)
    yield
    sessions.shutdown()


app = FastAPI(title="Miru transcode worker", version="0.1.0", lifespan=lifespan)

# Required, not optional. hls.js pulls the manifest and every segment with XHR
# from the web origin, so without this the player fails with an opaque CORS
# error and no video — the same failure mode a bare <video src> never has.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_methods=["GET", "DELETE"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {
        "ok": True,
        "encoder": sessions.ENCODER,
        "active_sessions": len(sessions.active()),
        "max_sessions": settings.max_sessions,
    }


@app.get("/hls")
async def start(
    src: str = Query(..., description="Source URL — must match an allowed prefix"),
    height: int | None = Query(None, description="Source height, to cap the ladder"),
    copy_video: bool = Query(False, description="transcode_audio: pass the video through"),
):
    """Start (or join) an encode and redirect to its master playlist.

    Idempotent: the session id is derived from the source, so a second request
    for the same file joins the running encode instead of starting another.
    """
    if not is_allowed(src, settings.allowed_prefixes):
        # The worker fetches whatever it is given; without this it is an SSRF
        # hole open to anything that can reach the tailnet.
        raise HTTPException(403, "source URL is not in the allowed list")

    try:
        session = await sessions.ensure(src, height, copy_video)
    except TimeoutError as exc:
        raise HTTPException(504, f"transcode did not start in time: {exc}") from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc

    return RedirectResponse(f"/hls/{session.sid}/master.m3u8", status_code=302)


@app.get("/hls/{sid}/{path:path}")
def serve(sid: str, path: str):
    """Serve a playlist or segment from a session directory."""
    root = (settings.cache / sid).resolve()
    target = (root / path).resolve()

    # Path traversal guard: `path` comes straight off the URL.
    if not str(target).startswith(str(root)) or not target.is_file():
        raise HTTPException(404, "no such playlist or segment")

    return FileResponse(
        target,
        media_type=MEDIA_TYPES.get(target.suffix, "application/octet-stream"),
        # Segments are immutable; playlists grow while the encode runs.
        headers={"Cache-Control": "no-cache" if target.suffix == ".m3u8"
                 else "public, max-age=31536000"},
    )


@app.delete("/hls/{sid}")
def stop(sid: str):
    """Cancel an encode. Used when a viewer leaves so the GPU is not held."""
    sessions._drop(sid)
    return {"stopped": sid}
