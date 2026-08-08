"""Who a work actually is: its provider identity, and the art that comes with it.

Torrent indexers carry no artwork and no identity — a release name is a
filename. Three sources, chosen so the common cases need no configuration at
all:

- **AniList** for anime. Free, no key, no registration, and its search tolerates
  the romaji/English/abbreviated titles that fall out of release names.
- **TVmaze** for series. Free, no key.
- **TMDB** for film. Needs a free key, and is the only one of the three that
  does. Checked: iTunes' search API returns nothing usable from here and TVmaze
  does not cover film, so there is no keyless option for movies worth shipping.

Enrichment never blocks ingest. A work appears on the wall the moment it is
seen, with `ArtTile` standing in, and picks up art whenever the backfill next
runs. A missing TMDB key degrades film to title cards rather than breaking
anything.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from miru.catalog.models import CatalogWork
from miru.core.config import settings

log = logging.getLogger(__name__)

UA = {"User-Agent": "Miru/0.1 (personal media server)"}

ANILIST = "https://graphql.anilist.co"
TVMAZE = "https://api.tvmaze.com"
TMDB = "https://api.themoviedb.org/3"

# Only these hosts are ever fetched. The URLs come from third-party API
# responses, so without an allowlist those services choose what our server
# requests — the same server-side request forgery shape the transcode worker
# already defends against.
ALLOWED_IMAGE_HOSTS = {
    "s4.anilist.co",
    "static.tvmaze.com",
    "image.tmdb.org",
}


def _get(url: str, data: bytes | None = None, headers: dict | None = None):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=20) as res:
        return json.loads(res.read().decode())


def _anilist(title: str) -> dict | None:
    query = """
    query ($s: String) {
      Media(search: $s, type: ANIME) {
        id
        format
        episodes
        title { romaji english }
        coverImage { extraLarge }
        bannerImage
        description(asHtml: false)
        averageScore
        genres
        startDate { year }
      }
    }"""
    try:
        body = json.dumps({"query": query, "variables": {"s": title}}).encode()
        d = _get(ANILIST, body, {"Content-Type": "application/json", "Accept": "application/json"})
        m = (d.get("data") or {}).get("Media")
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError):
        return None
    if not m:
        return None

    return {
        "provider": "anilist",
        "provider_id": str(m["id"]),
        "display_title": (m["title"].get("english") or m["title"].get("romaji") or title),
        "poster_url": (m.get("coverImage") or {}).get("extraLarge"),
        "backdrop_url": m.get("bannerImage"),
        "overview": (m.get("description") or "").replace("<br>", " ").strip() or None,
        # AniList scores out of 100; ours is out of 10 so one bar means one
        # thing whichever source filled it.
        "score": (m["averageScore"] / 10) if m.get("averageScore") else None,
        "genres": m.get("genres") or [],
        "year": (m.get("startDate") or {}).get("year"),
        # TV | MOVIE | OVA | ONA | SPECIAL. The anime rail tells a film from a
        # weekly show with this rather than with another filter pill.
        "format": m.get("format"),
        "episode_count": m.get("episodes"),
    }


def _tvmaze(title: str) -> dict | None:
    try:
        d = _get(f"{TVMAZE}/search/shows?q={urllib.parse.quote(title)}")
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None
    if not d:
        return None

    show = d[0].get("show") or {}
    image = show.get("image") or {}
    premiered = show.get("premiered") or ""
    return {
        "provider": "tvmaze",
        "provider_id": str(show.get("id")),
        "display_title": show.get("name") or title,
        "poster_url": image.get("original") or image.get("medium"),
        "backdrop_url": None,
        "overview": (show.get("summary") or "").replace("<p>", "").replace("</p>", "").strip()
        or None,
        "score": (show.get("rating") or {}).get("average"),
        "genres": show.get("genres") or [],
        "year": int(premiered[:4]) if premiered[:4].isdigit() else None,
        "format": "TV",
        "episode_count": None,
    }


def _tmdb(title: str, year: int | None, kind: str) -> dict | None:
    if not settings.tmdb_api_key:
        return None

    path = "search/movie" if kind == "movie" else "search/tv"
    params = {"api_key": settings.tmdb_api_key, "query": title, "include_adult": "false"}
    if year:
        params["year" if kind == "movie" else "first_air_date_year"] = year

    try:
        d = _get(f"{TMDB}/{path}?{urllib.parse.urlencode(params)}")
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        return None

    results = d.get("results") or []
    if not results:
        return None
    r = results[0]

    date = r.get("release_date") or r.get("first_air_date") or ""
    poster = r.get("poster_path")
    backdrop = r.get("backdrop_path")
    return {
        "provider": "tmdb",
        "provider_id": str(r.get("id")),
        "display_title": r.get("title") or r.get("name") or title,
        # w500 rather than original: these are 2:3 cards at ~190px, and a 2 MB
        # poster to fill 190px is bytes nobody sees.
        "poster_url": f"https://image.tmdb.org/t/p/w500{poster}" if poster else None,
        "backdrop_url": f"https://image.tmdb.org/t/p/w780{backdrop}" if backdrop else None,
        "overview": r.get("overview") or None,
        "score": r.get("vote_average"),
        "genres": [],
        "year": int(date[:4]) if date[:4].isdigit() else None,
        "format": "MOVIE" if kind == "movie" else "TV",
        "episode_count": None,
    }


def lookup(kind: str, title: str, year: int | None) -> dict | None:
    """Best available metadata for one work.

    Ordered by which source actually knows the thing, not by preference: AniList
    knows anime better than TMDB does, and TVmaze needs no key for series.
    """
    if kind == "anime":
        return _anilist(title) or _tmdb(title, year, "series")
    if kind == "series":
        return _tvmaze(title) or _tmdb(title, year, "series")
    return _tmdb(title, year, "movie")


def _merge_into(db: Session, loser: CatalogWork, winner: CatalogWork) -> None:
    """Fold one work into another, releases and all.

    Enrichment is exactly the moment two cards are revealed to be one thing: a
    release with no year in its name became its own work, and the metadata
    provider has just supplied the year that matches an existing one. Merging
    them is the wall's whole purpose rather than a workaround for the
    constraint.
    """
    from miru.catalog.models import CatalogRelease

    db.execute(
        update(CatalogRelease)
        .where(CatalogRelease.work_id == loser.id)
        .values(work_id=winner.id)
    )
    winner.release_count = (winner.release_count or 0) + (loser.release_count or 0)
    winner.best_seeder_pct = max(winner.best_seeder_pct or 0.0, loser.best_seeder_pct or 0.0)
    if loser.library_file_id and not winner.library_file_id:
        winner.library_file_id = loser.library_file_id
    if loser.download_job_id and not winner.download_job_id:
        winner.download_job_id = loser.download_job_id
    db.delete(loser)


def enrich_work(db: Session, work: CatalogWork) -> bool:
    """Resolve one work to a provider and fill it in. True if one was found.

    This is where a card gets its real identity, not just its poster. Two works
    that resolve to the same provider id are one show under two names, and the
    merge is the whole point of the pass rather than a side effect of it.
    """
    from miru.catalog.parse import normalised
    from miru.catalog.resolve import apply, resolve, work_by_provider

    data = resolve(db, work.kind, work.display_title, work.year)
    if not data:
        # Marked as attempted so the backfill does not ask about the same
        # untraceable release every half hour forever.
        work.provider = work.provider or "none"
        return False

    # The provider is the thing that knows *Youjo Senki* and *Saga of Tanya the
    # Evil* are one show. If it already has a card, this work IS that card.
    twin = work_by_provider(db, work.kind, data)
    if twin is None:
        # Taking the provider's title and year can also collide with a work that
        # already holds them — the same film, once from a release that named the
        # year and once from one that did not. That is a merge, not an error.
        key = normalised(data.get("display_title") or work.display_title)
        year = data.get("year") or work.year
        twin = db.execute(
            select(CatalogWork).where(
                CatalogWork.kind == work.kind,
                CatalogWork.normalised_title == key,
                CatalogWork.year.is_(None) if year is None else CatalogWork.year == year,
                CatalogWork.id != work.id,
            )
        ).scalar_one_or_none()

    if twin is not None and twin.id != work.id:
        _merge_into(db, work, twin)
        return True

    apply(work, data)
    return True


def backfill(db: Session, limit: int = 40) -> dict:
    """Resolve and fill the works nobody has asked a provider about yet.

    Bounded per pass on purpose. These are third-party APIs with rate limits
    (AniList allows 90 requests a minute), and the wall is usable without art,
    so there is nothing to gain by hammering them. `provider` is the mark that
    a work has been asked about — "none" included — so no work is asked twice.
    """
    pending = list(
        db.execute(
            select(CatalogWork)
            .where(CatalogWork.provider.is_(None))
            .order_by(CatalogWork.best_seeder_pct.desc())
            .limit(limit)
        ).scalars()
    )

    found = 0
    for work in pending:
        title = work.display_title
        # An earlier iteration may have folded this one into another card.
        if db.get(CatalogWork, work.id) is None:
            continue
        try:
            got = enrich_work(db, work)
            # Committed per work rather than per batch: a single constraint
            # violation used to roll back forty successful lookups with it.
            db.commit()
            if got:
                found += 1
        except Exception:  # noqa: BLE001 — one bad title must not stop the rest
            log.exception("enrichment failed for %r", title)
            db.rollback()
            fresh = db.get(CatalogWork, work.id)
            if fresh is not None:
                fresh.provider = "none"
                db.commit()

    if pending:
        log.info("enrichment: %d/%d works got art", found, len(pending))
    return {"attempted": len(pending), "found": found}
