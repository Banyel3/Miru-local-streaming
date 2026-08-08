"""The wall's API.

One endpoint serves the whole screen, because the rails are mutually exclusive
(see rails.py) and that rule cannot be applied if the client fetches each row
independently.
"""

from __future__ import annotations

import logging
import threading
import time

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from miru.acquisition.downloader import (
    configured,
    downloader,
    reachable,
    supports_streaming,
)
from miru.acquisition.provider import AcquisitionError
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

    The downloader, Prowlarr and the transcode worker all live on the PC. The
    wall is served from the laptop's Postgres, so it renders perfectly while
    every Download button would fail. Without this the page looks completely
    healthy and every action 502s.
    """
    global _pc_state
    checked, ok = _pc_state
    now = time.monotonic()
    if now - checked < _PC_TTL:
        return ok

    ok = reachable()
    _pc_state = (now, ok)
    return ok


def _candidates(releases: list[CatalogRelease]) -> list[Candidate]:
    return [
        Candidate(
            id=r.info_hash,
            title=r.title,
            indexer=r.indexer,
            seeders=r.seeders,
            size_bytes=r.size_bytes,
            quality=r.quality,
            group=r.release_group,
            grabbable=r.grabbable,
            stale=r.stale,
            episode=r.episode,
            episode_end=r.episode_end,
            complete=bool(r.complete),
        )
        for r in releases
    ]


def _collected_group(work: CatalogWork, releases: list[CatalogRelease]) -> str | None:
    """The release group this show is already being collected from, if any.

    The download job id *is* the infohash (see qbittorrent.py), so the release
    that was grabbed is the one it names. Only the latest grab is recorded, and
    that is exactly the affinity wanted: whatever source this show is currently
    coming from, not every source it ever came from.
    """
    return next(
        (r.release_group for r in releases if r.info_hash == work.download_job_id and r.release_group),
        None,
    )


def _work_json(w: CatalogWork) -> dict:
    return {
        "id": w.id,
        "kind": w.kind,
        "title": w.display_title,
        "year": w.year,
        # TV | MOVIE | OVA | ONA | SPECIAL, from the provider. How a film is
        # told from a weekly show inside the anime rail, instead of a fifth
        # filter pill that would not fit at 375px anyway.
        "format": w.format,
        # The provider's episode count against the releases actually held: the
        # honest "8 of 1,172" rather than 1,164 dead rows.
        "episode_count": w.episode_count,
        # Our own path, not the provider's. The browser makes no third-party
        # requests, and a miss 404s so the client can fall back to ArtTile.
        "poster_url": f"/api/posters/{w.id}" if w.poster_url else None,
        "overview": w.overview,
        "score": w.score,
        "release_count": w.release_count,
        "best_seeder_pct": round(w.best_seeder_pct, 3),
        "latest_release_at": w.latest_release_at.isoformat() if w.latest_release_at else None,
        "library_file_id": w.library_file_id,
        "download_job_id": w.download_job_id,
    }


def _release_json(r: CatalogRelease) -> dict:
    return {
        "info_hash": r.info_hash,
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
    for rail in rails.rails_for(kind):
        items, cursor = rails.page(db, rail.sort, kind, exclude=seen, rail=rail.key)
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

    # Anime and series get art with no configuration; film needs a TMDB key and
    # is the only one that does. Without this the film rails look broken next to
    # the anime rails and nothing says why.
    films_without_art = db.execute(
        select(func.count())
        .select_from(CatalogWork)
        .where(CatalogWork.kind == "movie", CatalogWork.poster_url.is_(None))
    ).scalar()

    return {
        "kind": kind,
        # False means Download and live search are dead, whatever the wall looks
        # like. The UI says so once at the top rather than per card.
        "pc_reachable": pc_reachable(),
        # Told apart deliberately: an unreachable PC needs waking, an
        # unconfigured downloader needs installing, and sending someone to do
        # the first when they need the second wastes their evening.
        "downloader_configured": configured(),
        "streaming": supports_streaming(),
        "empty": total is None,
        "refreshed_at": last.finished_at.isoformat() if last and last.finished_at else None,
        "refresh_error": last.error if last else None,
        "rails": out,
        "note": _sparse_note(kind, out),
        "artwork": {
            "tmdb_configured": bool(settings.tmdb_api_key),
            "films_without_art": films_without_art,
        },
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

    items, next_cursor = rails.page(db, rail.sort, kind, cursor=cursor, rail=rail.key)
    return {"items": [_work_json(w) for w in items], "next_cursor": next_cursor}


def sweep_now(work_id: int) -> None:
    """Run a pack sweep for one work, on its own session."""
    from miru.catalog.sweep import sweep
    from miru.core.db import SessionLocal

    db = SessionLocal()
    try:
        work = db.get(CatalogWork, work_id)
        if work is not None:
            sweep(db, work)
            db.commit()
    except Exception:  # noqa: BLE001 — a sweep must never take anything down
        log.exception("pack sweep failed for work %s", work_id)
        db.rollback()
    finally:
        db.close()


def _request_sweep(work_id: int) -> None:
    """Start one, off the request thread. Cheap when nothing is due."""
    threading.Thread(target=sweep_now, args=(work_id,), daemon=True).start()


@router.get("/works/{work_id}")
def work_detail(work_id: int, db: Session = Depends(get_db)):
    """A work, its three named choices, and every release behind them."""
    work = db.get(CatalogWork, work_id)
    if work is None:
        raise HTTPException(404, "no such work")

    # Look for complete packs of this show, in the background. The card is a
    # day-deep slice of the front page until something asks for the rest, and a
    # pack query takes seconds against four indexers — waiting on it would make
    # every card feel broken. What it finds appears on the next poll.
    _request_sweep(work_id)

    releases = list(
        db.execute(
            select(CatalogRelease)
            .where(CatalogRelease.work_id == work_id)
            .order_by(CatalogRelease.seeder_pct.desc())
        ).scalars()
    )
    by_hash = {r.info_hash: r for r in releases}
    choices = three_choices(_candidates(releases), _collected_group(work, releases))

    return {
        **_work_json(work),
        "pc_reachable": pc_reachable(),
        # Forty rows is not a choice anyone can make. Three named ones are.
        "choices": {
            name: (_release_json(by_hash[c.id]) if c and c.id in by_hash else None)
            for name, c in choices.items()
        },
        "releases": [_release_json(r) for r in releases],
        # Said up front rather than discovered after a download that never starts.
        "all_dead": all_viable_dead(_candidates(releases)),
    }


class Grab(BaseModel):
    info_hash: str | None = None
    watch: bool = True


@router.post("/works/{work_id}/download")
def start_download(work_id: int, grab: Grab, db: Session = Depends(get_db)):
    """Grab a release. With no guid, Miru picks — see rank.pick_default."""
    work = db.get(CatalogWork, work_id)
    if work is None:
        raise HTTPException(404, "no such work")
    if not configured():
        raise HTTPException(
            503, "No downloader is set up yet. Install qBittorrent on the PC and set "
                 "MIRU_QBITTORRENT_URL."
        )
    if not pc_reachable():
        raise HTTPException(503, "The PC is asleep, so downloads cannot start.")

    releases = list(
        db.execute(select(CatalogRelease).where(CatalogRelease.work_id == work_id)).scalars()
    )
    if grab.info_hash:
        chosen = next((r for r in releases if r.info_hash == grab.info_hash), None)
    else:
        picked = three_choices(_candidates(releases), _collected_group(work, releases))["best"]
        chosen = next((r for r in releases if picked and r.info_hash == picked.id), None)

    if chosen is None:
        raise HTTPException(409, "Nothing here can be downloaded.")

    try:
        # Built from the infohash rather than the stored link: Prowlarr's proxy
        # URL carries a payload it regenerates every response, so the one on
        # this row may already be dead.
        # `watch` is the user's intent, and it is the only thing that decides
        # piece order. Watch Now asks for sequential pieces so playback can
        # start before the last one lands; Download leaves libtorrent's normal
        # rarest-first selection alone, which is better for the swarm and for
        # total throughput.
        dl = downloader()
        if supports_streaming():
            job = dl.submit(chosen.magnet_uri, sequential=grab.watch)
        else:
            job = dl.submit(chosen.magnet_uri)
    except AcquisitionError as exc:
        raise HTTPException(502, str(exc)) from exc

    # Written now rather than derived later. Without it the in-library state
    # never fires and this work sits on the wall offering to download itself
    # again, forever.
    work.download_job_id = job.id
    db.commit()

    return {
        "job_id": job.id,
        "release": _release_json(chosen),
        "watch": grab.watch,
        # False means Watch Now can only honestly mean "when it finishes".
        "streaming": supports_streaming(),
    }


def _link_by_filename(db: Session, work: CatalogWork) -> None:
    """Point the card at the file the download actually produced.

    Matched on the name the downloader wrote, not on the release title. Those
    are different strings and matching them fuzzily does not work: a torrent
    called "[Erai-raws] Honzuki no Gekokujou: Shisho ni Naru Tame ni wa Shudan
    wo Erandeiraremasen" can contain a file called "[SubsPlease] Honzuki no
    Gekokujou S4 - 17 (1080p) [E56B1187].mkv". The downloader knows the answer,
    so ask it rather than guessing.
    """
    from miru.library.models import MediaFile

    try:
        info = downloader().playable_prefix(work.download_job_id)
    except Exception:  # noqa: BLE001 — the scan will retry on its next pass
        return

    name = (info.get("file") or info.get("name") or "").rsplit("/", 1)[-1]
    if not name:
        return

    row = db.execute(
        select(MediaFile).where(MediaFile.path.like(f"%/{name}"))
    ).scalar_one_or_none()
    if row is not None:
        work.library_file_id = row.id
        db.commit()
        log.info("linked %s to library file %s", work.display_title, row.id)


_scan_lock = threading.Lock()
_scan_thread: threading.Thread | None = None


def _request_scan() -> None:
    """Start a promotion scan unless one is already running."""
    global _scan_thread
    with _scan_lock:
        if _scan_thread is not None and _scan_thread.is_alive():
            return
        from miru.catalog.scheduler import scan_now

        def run():
            try:
                scan_now()
            except Exception:  # noqa: BLE001 — a failed scan must not 500 a poll
                log.exception("promotion scan failed")

        _scan_thread = threading.Thread(target=run, daemon=True)
        _scan_thread.start()


def _forgotten(w, why: str) -> dict:
    """A card whose download the downloader has no record of."""
    return {
        "work_id": w.id,
        "title": w.display_title,
        "job_id": w.download_job_id,
        "state": "failed",
        "error": why[:200],
        "progress": 0.0,
    }


@router.get("/downloads")
def downloads(db: Session = Depends(get_db)):
    """Every in-flight grab, in one request.

    One call for all of them rather than one per card: a wall with eight
    downloading cards polling individually is eight requests every two seconds
    to a machine that is already busy saturating its disk.
    """
    # Asked once, before touching the downloader at all. Without this the poll
    # tries every past download against a machine that is asleep, and the cost
    # is paid per work per poll.
    up = pc_reachable()
    if not up:
        return {"pc_reachable": False, "streaming": supports_streaming(), "downloads": []}

    dl = downloader()
    try:
        # One question for the whole screen, and — just as important — a
        # question about what the downloader is ACTUALLY doing rather than what
        # the catalogue remembers pointing at. A work holds one
        # download_job_id, so a merge of two cards that each had a download in
        # flight dropped one infohash, and that download became unreachable
        # from the UI while it carried on running. qBittorrent still knows.
        live = dl.statuses()
    except AttributeError:
        # aria2, the fallback backend. It cannot be watched while downloading
        # either, so it is not worth a bulk endpoint of its own.
        live = None
    except AcquisitionError as exc:
        log.warning("downloads poll failed: %s", exc)
        return {"pc_reachable": up, "streaming": supports_streaming(), "downloads": []}

    works = list(
        db.execute(select(CatalogWork).where(CatalogWork.download_job_id.isnot(None))).scalars()
    )

    out = []
    claimed = set()
    for w in works:
        job = (w.download_job_id or "").lower()
        claimed.add(job)
        s = live.get(job) if live is not None else None
        if live is None:
            try:
                s = dl.status(w.download_job_id)
            except AcquisitionError as exc:
                out.append(_forgotten(w, str(exc)))
                continue
        if s is None:
            # Deleted from the downloader directly. The card has to say so
            # rather than quietly disappearing, so the picker can be offered
            # again instead of the user thinking it is still coming.
            out.append(_forgotten(w, "The downloader no longer has this torrent."))
            continue

        # The moment a job reports done is the moment to promote it, rather than
        # letting it wait for the next periodic pass. Guarded so ten finishing
        # downloads start one scan, not ten.
        if s.state == "done" and w.library_file_id is None:
            _request_scan()
            _link_by_filename(db, w)

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

    # Downloads no card points at. Shown rather than hidden: they are running,
    # they are using the disk, and pause and cancel have to be reachable.
    for job, s in (live or {}).items():
        if job in claimed:
            continue
        out.append(
            {
                "work_id": None,
                # No card to take a title from, and "Unknown" with a pause
                # button next to it is not something anyone can act on.
                "title": s.name or job[:12],
                "job_id": job,
                "state": s.state,
                "progress": round(s.progress, 4),
                "speed_bps": s.speed_bps,
                "eta_seconds": s.eta_seconds,
                "error": s.error,
                "in_library": False,
            }
        )

    return {"pc_reachable": pc_reachable(), "streaming": supports_streaming(), "downloads": out}


@router.post("/downloads/{info_hash}/sequential")
def make_watchable(info_hash: str):
    """Switch a running download to sequential so it can be watched.

    This exists because intent changes: you queue something and then decide you
    want it tonight. With one downloader that is this one call. With two it
    would mean stopping, deleting and re-adding in the other one, losing
    everything downloaded so far.
    """
    if not supports_streaming():
        raise HTTPException(
            409, "The configured downloader can't be watched while downloading."
        )
    dl = downloader()
    try:
        dl.make_sequential(info_hash)
    except AcquisitionError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": True, "info_hash": info_hash}


class DownloadAction(BaseModel):
    action: str  # pause | resume | cancel


@router.post("/downloads/{job_id}/action")
def download_action(job_id: str, body: DownloadAction, db: Session = Depends(get_db)):
    """Pause, resume or stop a download.

    Cancel keeps the bytes. `deleteFiles=false` is not an oversight — a
    part-downloaded file may be one the user is already watching, and stopping a
    download is not a request to destroy it.
    """
    dl = downloader()
    if body.action not in {"pause", "resume", "cancel"}:
        raise HTTPException(422, "action must be pause, resume or cancel")

    try:
        if body.action == "cancel":
            dl.cancel(job_id)
            # The card must stop claiming a download that no longer exists.
            for w in db.execute(
                select(CatalogWork).where(CatalogWork.download_job_id == job_id)
            ).scalars():
                w.download_job_id = None
            db.commit()
        else:
            dl.set_paused(job_id, body.action == "pause")
    except AcquisitionError as exc:
        raise HTTPException(502, str(exc)) from exc
    return {"ok": True, "action": body.action}
