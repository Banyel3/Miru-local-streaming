import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from miru.core.auth import require_token
from miru.core.config import settings
from miru.core.db import create_all
from miru.acquisition.router import router as acquisition_router
from miru.catalog import scheduler
from miru.catalog.posters import router as posters_router
from miru.catalog.router import router as catalog_router
from miru.library.router import router as library_router
from miru.streaming.partial import router as live_router
from miru.streaming.router import router as streaming_router
from miru.streaming.subtitles import router as subtitles_router

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_all()
    scheduler.start()

    # Half-written remuxes from a killed process or a previous run. They are
    # full-size, so leaving them costs real disk — 4.8 GB had accumulated when
    # the per-request remux loop was found.
    try:
        from miru.streaming import remux

        if n := remux.reap():
            log.info("reaped %d abandoned remux part-files", n)
    except Exception:  # noqa: BLE001 — startup must not fail on housekeeping
        log.exception("could not reap remux cache")

    try:
        yield
    finally:
        await scheduler.stop()


app = FastAPI(title="Miru", version="0.1.0", lifespan=lifespan,
              dependencies=[Depends(require_token)])

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.web_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(acquisition_router)
app.include_router(catalog_router)
app.include_router(posters_router)
app.include_router(library_router)
app.include_router(streaming_router)
app.include_router(live_router)
app.include_router(subtitles_router)


def _storage_ok() -> bool:
    """Whether the media disk answers at all.

    A USB disk that drops re-enumerates under a new device node and the stale
    mount answers EIO to everything — the site half-breaks with nothing naming
    the cause. Cheap check, surfaced here so the UI can say the true thing.
    """
    import os

    try:
        return all(os.access(str(p), os.R_OK) and os.listdir(str(p)) is not None
                   for p in settings.libraries)
    except OSError:
        return False


@app.get("/api/health")
def health():
    return {
        "ok": True,
        "libraries": [str(p) for p in settings.libraries],
        "storage_ok": _storage_ok(),
    }
