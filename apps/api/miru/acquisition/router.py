from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from miru.acquisition.prowlarr import AcquisitionError, provider
from miru.acquisition.provider import DownloadStatus, SearchResult

router = APIRouter(prefix="/api/acquisition", tags=["acquisition"])


class Grab(BaseModel):
    result_id: str


@router.get("/search", response_model=list[SearchResult])
def search(q: str = Query(..., min_length=2), limit: int = Query(50, le=200)):
    """Search every configured indexer.

    Errors are surfaced rather than collapsed into an empty list: an empty grid
    that means "the indexers are down" and one that means "no matches" look
    identical to a user, and the previous search stack failed exactly that way.
    """
    try:
        return provider.search(q, limit)
    except AcquisitionError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.post("/download", response_model=dict)
def download(grab: Grab):
    try:
        job = provider.submit(grab.result_id)
    except AcquisitionError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"job_id": job.id}


@router.get("/download/{job_id}", response_model=DownloadStatus)
def status(job_id: str):
    try:
        return provider.status(job_id)
    except AcquisitionError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.delete("/download/{job_id}")
def cancel(job_id: str):
    try:
        provider.cancel(job_id)
    except AcquisitionError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"cancelled": job_id}
