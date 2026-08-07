import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from miru.core.auth import require_token
from miru.core.config import settings
from miru.core.db import create_all
from miru.library.router import router as library_router
from miru.streaming.router import router as streaming_router
from miru.streaming.subtitles import router as subtitles_router

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_all()
    yield


app = FastAPI(title="Miru", version="0.1.0", lifespan=lifespan,
              dependencies=[Depends(require_token)])

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.web_origin],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(library_router)
app.include_router(streaming_router)
app.include_router(subtitles_router)


@app.get("/api/health")
def health():
    return {"ok": True, "libraries": [str(p) for p in settings.libraries]}
