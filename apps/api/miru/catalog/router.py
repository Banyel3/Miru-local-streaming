"""The wall's API.

One endpoint serves the whole screen, because the rails are mutually exclusive
(see rails.py) and that rule cannot be applied if the client fetches each row
independently.
"""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from miru.acquisition.prowlarr import AcquisitionError, provider
from miru.catalog import rails
from miru.catalog.models import CatalogRefresh, CatalogRelease, CatalogWork
from miru.catalog.rank import Candidate, all_viable_dead, three_choices
from miru.core.config import settings
from miru.core.db import get_db

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/catalog", tags=["catalog"])

KINDS = {"all", "anime", "movie", "series"}

# Cached so a wall render is not three RPCs to a machine that may be asleep.
_pc_state: tuple[float, bool] = (0.0, False)
_PC_TTL = 20.0


def pc_reachable() -> bool:
    """Whether the PC can take a download right now.

    aria2, Prowlarr and the transcode worker all live on the PC. The wall is
    served from the laptop's Postgres, so it renders perfectly while every
    Download button would fail. Without this the page looks completely healthy
    and every action 502s.
    """
    global _pc_state
    checked, ok = _pc_state
    now = time.monotonic()
    if now - checked < _PC_TTL:
        return ok

    ok = False
    if settings.aria2_url:
        try:
            from miru.acquisition.prowlarr import _rpc

            _rpc("aria2.getVersion", [])
            ok = True
        except Exception:  # noqa: BLE001 — unreachable is the answer, not an error
            ok = False
    _pc_state = (now, ok)
    return ok


def _candidates(releases: list[CatalogRelease]) -> list[Candidate]:
    return [
        Candidate(
            id=r.guid,
            title=r.title,
            indexer=r.indexer,
            seeders=r.seeders,
            size_bytes=r.size_bytes,
            quality=r.quality,
            group=r.release_group,
            grabbable=r.grabbable,
            stale=r.stale,
        )
        for r in releases
    ]


def _work_json(w: CatalogWork) -> dict:
    return {
        "id": w.id,
        "kind": w.kind,
        "title": w.display_title,
        "year": w.year,
        "poster_url": w.poster_url,
        "overview": w.overview,
        "score": w.score,
        "release_count": w.release_count,
        "best_seeder_pct": round(w.best_seeder_pct, 3),
        "library_file_id": w.library_file_id,
        "download_job_id": w.download_job_id,
    }


def _release_json(r: CatalogRelease) -> dict:
    return {
        "guid": r.guid,
        "title": r.title,
        "indexer": r.indexer,
        "quality": r.quality,
        "group": r.release_group,
        "size_bytes": r.size_bytes,
        "seeders": r.seeders,
        "season": r.season,
        "episode": r.episode,
        "episode_end": r.episode_end,
        # The claim only Miru can make: whether grabbing this wakes the PC.
        "needs_pc": r.predicted_strategy == "transcode_full",
        "predicted_strategy": r.predicted_strategy,
        "stale": r.stale,
        "grabbable": r.grabbable,
    }


@router.get("")
def wall(
    kind: str = Query("all"),
    db: Session = Depends(get_db),
):
    """Everything the browse screen needs, in one response."""
    if kind not in KINDS:
        raise HTTPException(422, f"kind must be one of {sorted(KINDS)}")

    last = db.execute(
        select(CatalogRefresh).order_by(CatalogRefresh.id.desc()).limit(1)
    ).scalar_one_or_none()

    seen: set[int] = set()
    out = []
    for rail in rails.RAILS:
        items, cursor = rails.page(db, rail.sort, kind, exclude=seen)
        seen.update(w.id for w in items)
        out.append(
            {
                "key": rail.key,
                "title": rail.title,
                "jp": rail.jp,
                "layout": rails.layout_for(len(items), bool(cursor)),
                "items": [_work_json(w) for w in items],
                "next_cursor": cursor,
            }
        )

    total = db.execute(select(CatalogWork).limit(1)).scalar_one_or_none()

    return {
        "kind": kind,
        # False means Download and live search are dead, whatever the wall looks
        # like. The UI says so once at the top rather than per card.
        "pc_reachable": pc_reachable(),
        "empty": total is None,
        "refreshed_at": last.finished_at.isoformat() if last and last.finished_at else None,
        "refresh_error": last.error if last else None,
        "rails": out,
        "note": _sparse_note(kind, out),
    }


def _sparse_note(kind: str, out: list[dict]) -> str | None:
    """Why a row is short, said plainly.

    Sparseness here is a source problem, but it still has a design answer: tell
    the user which indexer is missing and where to add one.
    """
    if kind != "series":
        return None
    if sum(len(r["items"]) for r in out) >= rails.RAIL_MINIMUM:
        return None
    return (
        "Only one of your indexers covers TV, so this is thin. "
        "Adding a TV indexer in Prowlarr fills it out."
    )


@router.get("/rail/{key}")
def rail_page(
    key: str,
    kind: str = Query("all"),
    cursor: str | None = Query(None),
    db: Session = Depends(get_db),
):
    """The next page of one rail. The client keeps one page in hand."""
    rail = next((r for r in rails.RAILS if r.key == key), None)
    if rail is None:
        raise HTTPException(404, f"no rail named {key}")

    items, next_cursor = rails.page(db, rail.sort, kind, cursor=cursor)
    return {"items": [_work_json(w) for w in items], "next_cursor": next_cursor}


@router.get("/works/{work_id}")
def work_detail(work_id: int, db: Session = Depends(get_db)):
    """A work, its three named choices, and every release behind them."""
    work = db.get(CatalogWork, work_id)
    if work is None:
        raise HTTPException(404, "no such work")

    releases = list(
        db.execute(
            select(CatalogRelease)
            .where(CatalogRelease.work_id == work_id)
            .order_by(CatalogRelease.seeder_pct.desc())
        ).scalars()
    )
    by_guid = {r.guid: r for r in releases}
    choices = three_choices(_candidates(releases))

    return {
        **_work_json(work),
        "pc_reachable": pc_reachable(),
        # Forty rows is not a choice anyone can make. Three named ones are.
        "choices": {
            name: (_release_json(by_guid[c.id]) if c and c.id in by_guid else None)
            for name, c in choices.items()
        },
        "releases": [_release_json(r) for r in releases],
        # Said up front rather than discovered after a download that never starts.
        "all_dead": all_viable_dead(_candidates(releases)),
    }


class Grab(BaseModel):
    release_guid: str | None = None
    watch: bool = True


@router.post("/works/{work_id}/download")
def start_download(work_id: int, grab: Grab, db: Session = Depends(get_db)):
    """Grab a release. With no guid, Miru picks — see rank.pick_default."""
    work = db.get(CatalogWork, work_id)
    if work is None:
        raise HTTPException(404, "no such work")
    if not pc_reachable():
        raise HTTPException(503, "The PC is asleep, so downloads cannot start.")

    releases = list(
        db.execute(select(CatalogRelease).where(CatalogRelease.work_id == work_id)).scalars()
    )
    if grab.release_guid:
        chosen = next((r for r in releases if r.guid == grab.release_guid), None)
    else:
        picked = three_choices(_candidates(releases))["best"]
        chosen = next((r for r in releases if picked and r.guid == picked.id), None)

    if chosen is None:
        raise HTTPException(409, "Nothing here can be downloaded.")

    try:
        job = provider.submit(chosen.magnet or chosen.download_url)
    except AcquisitionError as exc:
        raise HTTPException(502, str(exc)) from exc

    # Written now rather than derived later. Without it the in-library state
    # never fires and this work sits on the wall offering to download itself
    # again, forever.
    work.download_job_id = job.id
    db.commit()

    return {"job_id": job.id, "release": _release_json(chosen), "watch": grab.watch}


@router.get("/downloads")
def downloads(db: Session = Depends(get_db)):
    """Every in-flight grab, in one request.

    One call for all of them rather than one per card: a wall with eight
    downloading cards polling individually is eight requests every two seconds
    to a machine that is already busy saturating its disk.
    """
    works = list(
        db.execute(select(CatalogWork).where(CatalogWork.download_job_id.isnot(None))).scalars()
    )
    if not works:
        return {"pc_reachable": pc_reachable(), "downloads": []}

    out = []
    for w in works:
        try:
            s = provider.status(w.download_job_id)
        except AcquisitionError as exc:
            # A job the downloader has forgotten is not an error worth failing
            # the whole poll over — the card shows it as failed and offers the
            # picker again.
            out.append(
                {
                    "work_id": w.id,
                    "title": w.display_title,
                    "job_id": w.download_job_id,
                    "state": "failed",
                    "error": str(exc)[:200],
                    "progress": 0.0,
                }
            )
            continue

        out.append(
            {
                "work_id": w.id,
                "title": w.display_title,
                "job_id": w.download_job_id,
                "state": s.state,
                "progress": round(s.progress, 4),
                "speed_bps": s.speed_bps,
                "eta_seconds": s.eta_seconds,
                "error": s.error,
                # aria2 finishing is not the same as the file being playable:
                # the mover has to promote it out of incoming and the scan has
                # to index it. Without this gap being named, the card snaps back
                # to Download and the user grabs it twice.
                "in_library": w.library_file_id is not None,
            }
        )
    return {"pc_reachable": pc_reachable(), "downloads": out}
