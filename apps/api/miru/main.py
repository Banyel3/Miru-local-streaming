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


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_all()
    scheduler.start()
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


@app.get("/api/health")
def health():
    return {"ok": True, "libraries": [str(p) for p in settings.libraries]}
