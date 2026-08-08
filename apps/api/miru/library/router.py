from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, model_validator
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from miru.core.db import get_db
from miru.library.models import Job, MediaFile
from miru.library.scanner import run_scan_job
from miru.library import series as series_mod
from miru.streaming import remux
from miru.transcode.worker import NEEDS_WORKER, availability, hls_url

router = APIRouter(prefix="/api", tags=["library"])


class MediaFileOut(BaseModel):
    """M1 stands in for Series until the metadata module lands."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    path: str
    size_bytes: int
    duration_ms: int | None
    container: str | None
    video_codec: str | None
    audio_codec: str | None
    audio_channels: int | None
    width: int | None
    height: int | None
    subtitle_streams: list[dict]
    playback_strategy: str

    # Derived per request from (strategy, worker reachable) — never stored.
    # A stored value goes stale the moment the PC comes back.
    availability: str = "available"
    availability_note: str | None = None

    # Absolute worker URL, handed to the browser so it can fetch HLS directly.
    # Deliberately NOT a redirect from this API: a cross-origin CORS redirect
    # taints the Origin header to `null`, which no allowlist on the worker can
    # ever match, so the manifest fetch fails no matter how CORS is configured.
    hls_url: str | None = None

    @model_validator(mode="after")
    def _derive_availability(self):
        self.availability, self.availability_note = availability(self.playback_strategy)
        if self.playback_strategy == "remux":
            from pathlib import Path as _P

            st = remux.state(self.id, _P(self.path))
            if st == "working":
                self.availability = "preparing"
                self.availability_note = "Repackaging this for playback — a few seconds."
            elif st == "failed":
                self.availability = "unavailable"
                self.availability_note = remux.error(self.id) or "Couldn't repackage this file."
        if self.playback_strategy in NEEDS_WORKER and self.availability == "gpu-ready":
            self.hls_url = hls_url(self.id, self.playback_strategy, self.height)
        return self


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    type: str
    status: str
    payload: dict
    attempts: int
    error: str | None


@router.get("/library", response_model=list[MediaFileOut])
def library(q: str | None = None, sort: str = "title", db: Session = Depends(get_db)):
    stmt = select(MediaFile)
    if q:
        stmt = stmt.where(or_(MediaFile.title.ilike(f"%{q}%"), MediaFile.path.ilike(f"%{q}%")))
    order = {"title": MediaFile.title, "added": MediaFile.created_at.desc()}.get(sort, MediaFile.title)
    return db.scalars(stmt.order_by(order)).all()


@router.get("/files/{file_id}")
def file_detail(file_id: int, db: Session = Depends(get_db)):
    """One file, plus the show it belongs to and that show's episodes.

    The series half is what stops this page losing the poster and the real
    title the moment you arrive from the wall, and it is what replaces "In this
    folder" — which grouped by directory and so listed every unrelated file in
    a flat media folder.
    """
    record = db.get(MediaFile, file_id)
    if not record:
        raise HTTPException(404, "no such file")

    payload = MediaFileOut.model_validate(record, from_attributes=True).model_dump()

    work = series_mod.work_for_file(db, file_id)
    # A file with no catalogue entry is shown alone. Guessing its series from
    # the filename is exactly what produced four cards for one show.
    payload["series"] = series_mod.series_payload(work) if work else None
    payload["episodes"] = series_mod.episodes_for(db, work) if work else []
    return payload


@router.post("/library/scan", response_model=JobOut, status_code=202)
def enqueue_scan(background: BackgroundTasks, db: Session = Depends(get_db)):
    job = Job(type="library.scan")
    db.add(job)
    db.commit()
    # ponytail: BackgroundTasks, not APScheduler. Arrives with periodic
    # rescans in M2 — the jobs row is already the durable part.
    background.add_task(run_scan_job, job.id)
    return job


@router.get("/jobs/{job_id}", response_model=JobOut)
def job_status(job_id: int, db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if not job:
        raise HTTPException(404, "no such job")
    return job
