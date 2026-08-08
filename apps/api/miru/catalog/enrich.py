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
import re
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


class ProviderUnreachable(Exception):
    """We could not ask, as distinct from having asked and been told no.

    The distinction is the whole point of this class. Enrichment records its
    misses so the backfill does not ask about the same unparseable release
    every half hour forever — but that record is permanent, and a work marked
    as asked is never selected again. Recording a dropped connection as a miss
    therefore retires a card for the life of the database. Measured: the wifi
    dropping for the forty seconds a pass runs marked forty works "none".
    """


def _unreachable(exc: Exception) -> bool:
    """Whether this failure means the provider never answered.

    A 404 IS an answer — TMDB says it that way for a title it does not have,
    and treating that as an outage would make the backfill retry a genuine miss
    forever, which is the bug this file already had in the other direction.
    Everything else — 5xx, 429, DNS, timeouts, a body that is not JSON — means
    the question did not land.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code not in (400, 401, 403, 404)
    return True


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
        isAdult
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
    except (urllib.error.URLError, OSError, json.JSONDecodeError, KeyError) as exc:
        if _unreachable(exc):
            raise ProviderUnreachable(f"anilist: {exc}") from exc
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
        # The provider's own word for it. Nyaa files adult anime under
        # TV/Anime like everything else, so the category cannot see it and the
        # wall was showing it — the category is not even wrong, it really is
        # anime. Missing means unknown, and unknown is not adult: assuming
        # otherwise would empty the wall of everything not yet answered about.
        "adult": bool(m.get("isAdult")),
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
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        if _unreachable(exc):
            raise ProviderUnreachable(f"tvmaze: {exc}") from exc
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
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        if _unreachable(exc):
            raise ProviderUnreachable(f"tmdb: {exc}") from exc
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
        # Namespaced, because TMDB numbers films and television separately:
        # movie 550 and tv 550 are unrelated shows. A provider id is now matched
        # without the kind beside it, so it has to be unique on its own or the
        # two would merge into one card offering the wrong download.
        "provider_id": f"{'movie' if kind == 'movie' else 'tv'}:{r.get('id')}",
        "adult": bool(r.get("adult")),
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
    """The sources that might know this title, best first.

    AniList is asked first whatever the indexer called it. It holds anime and
    nothing else, so an answer from it is a claim about the show rather than
    about the indexer that happened to carry the release — and the indexers do
    not agree with each other. Measured on the live catalogue, one show:

        series   The Pirate Bay   88 releases   (carries no anime tag at all)
        anime    Nyaa.si          43            (5070 TV/Anime)

    Asking TVmaze first for the series half got a correct answer to the wrong
    question: it IS a television show, so both halves resolved happily and
    never met. Frieren was two cards in two rails holding 141 releases between
    them. The cost of asking AniList first is one miss on live-action, which is
    a cached miss; the gain is that the kind stops being the indexer's opinion.

    After that the category still orders things, because it is a good hint even
    when it is not a fact.
    """
    anilist = (lambda t, y: _anilist(t),)
    if kind == "anime":
        return anilist + (lambda t, y: _tmdb(t, y, "series"),)
    if kind == "series":
        return anilist + (lambda t, y: _tvmaze(t), lambda t, y: _tmdb(t, y, "series"))
    return anilist + (lambda t, y: _tmdb(t, y, "movie"),)


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
    if not _could_name_a_show(title):
        return None

    words = title.split()
    sources = _sources(kind)
    down = 0
    for source in sources:
        for n in range(len(words), 0, -1):
            try:
                data = source(" ".join(words[:n]), year)
            except ProviderUnreachable as exc:
                # One source being down is not this work being unresolvable:
                # AniList is asked first for anime, and if it is out while
                # TVmaze is up the card should still get its art. Give up on
                # this source and try the next.
                log.debug("source unreachable, trying the next: %s", exc)
                down += 1
                break
            if data is None:
                continue
            # Checked whatever the length. The full-length search used to be
            # accepted unquestioned, on the reasoning that we asked exactly
            # what the release said — but a badly parsed title is full-length
            # by definition, and that is precisely where the worst answers came
            # from. `CHS` was answered with *CHS: Dash for the Cash* and an
            # episode of Frieren went onto the wall as an American show.
            if _names_the_same_thing(data, title):
                return data
            # A shortened search that found something else. "Detective Conan
            # Movie 2 The Fourteenth Target" shortens to a *different* Conan
            # film, and a merged card offering the wrong download is worse than
            # the split it would have fixed — see parse.py.
            break

    if down and down == len(sources):
        # Nobody answered. Saying "not found" here would be a lie the caller
        # writes down permanently.
        raise ProviderUnreachable(f"all {down} sources unreachable for {title!r}")
    return None


# One short token names no show. `CHS` is a subtitle language, `v2` is a
# re-release marker, `01` is an episode. Asking a provider about any of them is
# a lottery whose prize is a wrong card, and a wrong card offers the wrong
# download — the one failure this catalogue refuses to trade a split for.
_UNIDENTIFIABLE = re.compile(r"^\s*(?:[A-Za-z]{1,4}|\d{1,4}|v\d+)\s*$", re.I)


def _could_name_a_show(title: str) -> bool:
    """Whether this is worth asking a provider about at all.

    Short real titles exist — *Akira*, *BLEACH*, *Monster* — so the rule is not
    about length. It is about a single token so generic that a match would be a
    coincidence rather than a recognition.
    """
    t = (title or "").strip()
    if len(t) < 2:
        return False
    return not _UNIDENTIFIABLE.fullmatch(t)


def _loose(text: str) -> str:
    """A name reduced to what two sources would agree on.

    Scene naming drops the punctuation a provider keeps — `Journey's` is
    written `Journeys` — and a strict comparison called those different shows.
    Measured: the resolve rate fell from 83% to 14% on that alone, and the
    single largest loss was 102 releases of one show.
    """
    return re.sub(r"[^a-z0-9]+", "", normalised(text))


def _names_the_same_thing(data: dict, title: str) -> bool:
    """Whether the answer is about the show that was asked about.

    Containment in either direction, on the loose form. Either direction
    because a provider legitimately answers with a longer canonical title than
    the one asked about — the release says `Frieren`, the record is called
    `Frieren: Beyond Journey's End` — and with a shorter one when the release
    name carries a season or a subtitle the record does not.

    An answer carrying no names at all cannot be checked, so it is accepted:
    rejecting it would throw away a real match on the strength of a missing
    field. All three fetchers populate `names` from what the provider returned,
    so that is the unverifiable case rather than the normal one.
    """
    names = [n for n in data.get("names") or [] if n]
    if not names:
        return True
    key = _loose(title)
    if not key:
        return False
    return any(_overlap(key, _loose(name)) for name in names)


# Below this, one name appearing inside another is coincidence rather than
# recognition: `chs` sits inside `chsdashforthecash` and names nothing about it,
# while `frieren` inside `frierenbeyondjourneysend` is the same show. Five
# characters is where the live catalogue separates the two.
_MIN_OVERLAP = 5


def _overlap(key: str, name: str) -> bool:
    if not name:
        return False
    if key == name:
        return True
    shorter, longer = sorted((key, name), key=len)
    return len(shorter) >= _MIN_OVERLAP and shorter in longer


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

    # ProviderUnreachable propagates on purpose: backfill treats it as "come
    # back later" rather than writing the mark that retires this work.
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
        except ProviderUnreachable as exc:
            # The network, not this title. Abandon the pass with every
            # remaining work still unmarked: the scheduler runs again in half
            # an hour, and by then the wifi is usually back. Continuing would
            # spend the rest of the batch failing the same way.
            log.warning("enrichment paused, providers unreachable: %s", exc)
            db.rollback()
            break
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
