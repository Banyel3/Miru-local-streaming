"""Joining a library file to the show it belongs to.

The library and the catalogue knew nothing about each other. The wall knew about
shows, posters and episodes; a `media_files` row knew about a path on disk. So
clicking a card crossed that line and lost the artwork, the real title, and any
sense of what else exists of that show — and the page filled the gap with "In
this folder", which grouped by directory and therefore listed every unrelated
file in a flat media folder.

The link already existed in one direction: CatalogWork.library_file_id, written
when a download is promoted. This reads it backwards.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from miru.catalog.models import CatalogRelease, CatalogWork
from miru.library.models import MediaFile


def work_for_file(db: Session, file_id: int) -> CatalogWork | None:
    return db.execute(
        select(CatalogWork).where(CatalogWork.library_file_id == file_id)
    ).scalar_one_or_none()


def series_payload(work: CatalogWork) -> dict:
    return {
        "work_id": work.id,
        "title": work.display_title,
        "year": work.year,
        "kind": work.kind,
        "overview": work.overview,
        "score": work.score,
        # Our own proxy path, never the provider's URL — same rule as the wall,
        # so the browser makes no third-party requests and a miss 404s.
        "poster_url": f"/api/posters/{work.id}" if work.poster_url else None,
    }


def episodes_for(db: Session, work: CatalogWork) -> list[dict]:
    """Every episode of this show, owned and available in one list.

    Two sources deliberately merged: files already on disk, and releases the
    indexers are still offering. One screen then answers "what do I have" and
    "what can I get" together, and the difference is a marker on a row rather
    than two places to look.
    """
    releases = list(
        db.execute(select(CatalogRelease).where(CatalogRelease.work_id == work.id)).scalars()
    )
    owned_files = {
        f.id: f
        for f in db.execute(
            select(MediaFile).where(
                MediaFile.id.in_(
                    select(CatalogWork.library_file_id).where(
                        CatalogWork.id == work.id, CatalogWork.library_file_id.isnot(None)
                    )
                )
            )
        ).scalars()
    }

    rows: dict[tuple, dict] = {}
    for r in releases:
        if r.episode is None:
            continue
        # A batch is one row and its own scope. You cannot download episode 743
        # alone out of a 741-746 torrent, so exploding it into six rows would be
        # offering something that does not exist.
        key = (r.episode, r.episode_end)
        row = rows.setdefault(
            key,
            {
                "episode": r.episode,
                "episode_end": r.episode_end,
                "releases": 0,
                "owned": False,
                "file_id": None,
                "quality": r.quality,
            },
        )
        row["releases"] += 1

    for fid, f in owned_files.items():
        # The owned file is one of these episodes; match it to its row when the
        # release it came from named one.
        match = next(
            (r for r in releases if r.episode is not None and work.library_file_id == fid),
            None,
        )
        if match is None:
            continue
        key = (match.episode, match.episode_end)
        if key in rows:
            rows[key]["owned"] = True
            rows[key]["file_id"] = fid

    # Newest first: what a weekly watcher wants, and where a backlog watcher
    # resumes.
    return sorted(rows.values(), key=lambda r: r["episode"], reverse=True)
