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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from miru.catalog.models import CatalogWork
from miru.catalog.parse import normalised
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


# AniList's published limit is 90 requests a minute, and it answers 429 for the
# rest of the minute once you pass it — so the pass that trips it enriches
# nothing. One gate for all three sources rather than a limit each: TMDB would
# tolerate far more, and nothing here is fast enough for that to matter.
_MIN_INTERVAL = 60 / 75
_lock = threading.Lock()
_last_call = 0.0


def _throttle() -> None:
    global _last_call
    with _lock:
        wait = _MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)
        _last_call = time.monotonic()


def _get(url: str, data: bytes | None = None, headers: dict | None = None):
    _throttle()
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
        title { romaji english native }
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
        # Every name this work goes by. A shortened search is only believed when
        # one of these is really in the release title, and the release names
        # romaji where AniList shows English.
        "names": [n for n in m["title"].values() if n],
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
        "names": [show.get("name") or title],
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
        "names": [n for n in (r.get("title"), r.get("name"), r.get("original_title")) if n],
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


def _sources(kind: str):
    """The sources that know this kind, best first.

    Ordered by which one actually knows the thing, not by preference: AniList
    knows anime better than TMDB does, and TVmaze needs no key for series.
    """
    if kind == "anime":
        return (lambda t, y: _anilist(t), lambda t, y: _tmdb(t, y, "series"))
    if kind == "series":
        return (lambda t, y: _tvmaze(t), lambda t, y: _tmdb(t, y, "series"))
    return (lambda t, y: _tmdb(t, y, "movie"),)


def lookup(kind: str, title: str, year: int | None) -> dict | None:
    """Best available metadata for one work.

    Each source is given the full title and then progressively shorter ones
    before the next source is asked anything at all. Release names carry tails
    no provider has heard of — "NCOP+NCED", "Tagalog", "S01E1172", "1985 LDRip"
    — and 33 of 135 distinct anime titles in the live catalogue missed on the
    full string; shortening recovers 15 of them, *Ore Monogatari!!* and
    *One Piece* among them.

    Exhausting one source first matters as much as the shortening does. AniList
    misses "Youjo Senki 幼女戦記" on the full string and TMDB matches it, so
    asking TMDB before AniList has been given "Youjo Senki" puts the same show
    on two cards under two providers — the exact split this is meant to close.
    """
    words = title.split()
    for source in _sources(kind):
        for n in range(len(words), 0, -1):
            data = source(" ".join(words[:n]), year)
            if data is None:
                continue
            if n == len(words) or _names_the_same_thing(data, title):
                return data
            # A shortened search that found something else. "Detective Conan
            # Movie 2 The Fourteenth Target" shortens to a *different* Conan
            # film, and a merged card offering the wrong download is worse than
            # the split it would have fixed — see parse.py.
            break
    return None


def _names_the_same_thing(data: dict, title: str) -> bool:
    """Whether a shortened search found what the release actually names."""
    key = normalised(title)
    return any(normalised(n) in key for n in data.get("names") or [] if n)


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
            select(CatalogWork.id)
            .where(CatalogWork.provider.is_(None))
            .order_by(CatalogWork.best_seeder_pct.desc())
            .limit(limit)
        ).scalars()
    )

    found = 0
    for work_id in pending:
        # Ids, and the row's existence asked of the database rather than of the
        # session. A work an earlier iteration folded into another card is gone,
        # and modifying a held instance of it fails the flush with
        # StaleDataError — which took the rest of the pass down with it.
        if db.execute(
            select(CatalogWork.id).where(CatalogWork.id == work_id)
        ).scalar_one_or_none() is None:
            continue
        work = db.get(CatalogWork, work_id)
        title = work.display_title
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
            try:
                fresh = db.get(CatalogWork, work_id)
                if fresh is not None:
                    fresh.provider = "none"
                    db.commit()
            except Exception:  # noqa: BLE001 — nor may the recovery from it
                db.rollback()

    if pending:
        log.info("enrichment: %d/%d works got art", found, len(pending))
    return {"attempted": len(pending), "found": found}
