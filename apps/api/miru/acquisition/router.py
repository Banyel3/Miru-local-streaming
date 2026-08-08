import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from miru.acquisition.downloader import downloader, supports_streaming
from miru.acquisition.prowlarr import provider
from miru.acquisition.provider import AcquisitionError
from miru.acquisition.provider import DownloadStatus, SearchResult
from miru.catalog.ingest import ingest_search
from miru.core.db import get_db

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/acquisition", tags=["acquisition"])


class Grab(BaseModel):
    result_id: str
    # Whether the user wants to watch it rather than shelve it. Decides piece
    # order, and nothing else.
    watch: bool = False


@router.get("/search", response_model=list[SearchResult])
def search(
    q: str = Query(..., min_length=2),
    limit: int = Query(50, le=200),
    db: Session = Depends(get_db),
):
    """Search every configured indexer, and keep what comes back.

    Errors are surfaced rather than collapsed into an empty list: an empty grid
    that means "the indexers are down" and one that means "no matches" look
    identical to a user, and the previous search stack failed exactly that way.

    The results are also ingested, because a query is the only reach past the
    indexers' front page — measured at about one day, with `limit` ignored and
    `offset` returning nothing. Rendering and discarding them meant searching
    for a show told Miru nothing. The two-character minimum above and the
    grabbable/infohash filters inside ingest are what keep a broad query from
    writing junk.
    """
    try:
        results = provider.search(q, limit)
    except AcquisitionError as exc:
        raise HTTPException(502, str(exc)) from exc

    try:
        ingest_search(db, results)
    except Exception:  # noqa: BLE001 — a failed write must not fail the search
        log.exception("could not ingest results for %r", q)
        db.rollback()
    return results


@router.post("/download", response_model=dict)
def download(grab: Grab):
    """Grab something straight from a live search.

    Goes through the configured downloader rather than Prowlarr's own, so a
    result found by searching behaves the same as one found on the wall.
    """
    dl = downloader()
    try:
        if supports_streaming():
            job = dl.submit(grab.result_id, sequential=grab.watch)
        else:
            job = dl.submit(grab.result_id)
    except AcquisitionError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"job_id": job.id, "streaming": supports_streaming()}


@router.get("/download/{job_id}", response_model=DownloadStatus)
def status(job_id: str):
    try:
        return downloader().status(job_id)
    except AcquisitionError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.delete("/download/{job_id}")
def cancel(job_id: str):
    try:
        downloader().cancel(job_id)
    except AcquisitionError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"cancelled": job_id}
