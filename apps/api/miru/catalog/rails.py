"""Rail queries: what each row of the wall contains.

Two rules live here rather than in the client, because both are easy to get
subtly wrong and impossible to notice when you do.

**Rails are mutually exclusive.** The catalog is small — one measured pass
produced 141 works — so Trending and Fresh drawn independently would show
largely the same titles in a different order, and three rows of One Piece is
worse than one. A work claimed by an earlier rail is skipped by every later one.

**Paging is keyset, not offset.** A refresh job inserts into this table every
thirty minutes, and offset paging over a concurrently-growing table silently
repeats and skips rows.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from sqlalchemy import Select, and_, case, or_, select
from sqlalchemy.orm import Session

from miru.catalog.models import CatalogWork

PAGE = 24

# Below this a row reads as broken rather than short, so it renders as a grid.
# Measured: only one configured indexer covers TV, and it yielded 13 works.
RAIL_MINIMUM = 8


@dataclass
class Rail:
    key: str
    title: str
    jp: str
    sort: str
    note: str | None = None
    # The walls this rail belongs on. None means all of them.
    only_for: tuple[str, ...] | None = None


# Order matters twice over. Rails are mutually exclusive, so the first one gets
# first pick of the catalogue — and it is the one the hero is drawn from. Latest
# leads because a wall whose top row is not in any obvious order reads as
# random, however good the ranking behind it is.
RAILS = [
    Rail("latest", "Latest releases", "新着", "latest"),
    Rail("trending", "Trending now", "人気", "trending"),
    # Films last, and only ever populated on the anime wall. The provider
    # decides what a show is now, so *Your Name* and *One Piece Film: Red*
    # resolve at AniList and carry kind=anime — correct, and it would leave a
    # film sitting between two weekly episode cards with nothing to say it is
    # one. AniList already draws the line: `format` is MOVIE for the film and TV
    # for the series. That is the fifth pill the wall does not have room for at
    # 375px, expressed as a row instead.
    Rail("films", "Films", "劇場版", "trending", only_for=("anime",)),
]


def rails_for(kind: str | None) -> list[Rail]:
    """The rails this wall actually has.

    Films is an anime-wall row: a live-action film already has its own kind, so
    on the Movies wall the row would either duplicate the wall or, filtered the
    other way, hold everything the wall was already showing.
    """
    return [r for r in RAILS if r.only_for is None or (kind in r.only_for)]


def _sort_value(work: CatalogWork, sort: str):
    if sort == "trending":
        return work.best_seeder_pct
    return (work.latest_release_at or work.first_seen_at).isoformat()


def encode_cursor(work: CatalogWork, sort: str) -> str:
    return base64.urlsafe_b64encode(f"{_sort_value(work, sort)}|{work.id}".encode()).decode()


def decode_cursor(cursor: str | None) -> tuple[str, int] | None:
    if not cursor:
        return None
    try:
        raw = base64.urlsafe_b64decode(cursor.encode()).decode()
        value, work_id = raw.rsplit("|", 1)
        return value, int(work_id)
    except (ValueError, TypeError):
        # A cursor we cannot read is a cursor from an older shape. Start over
        # rather than 400 — the user did not type it.
        return None


# The anime split. NULL is not MOVIE, but SQL will not say so — an unresolved
# work has no format at all, and `!= 'MOVIE'` drops every one of them off the
# wall. A weekly show is what most anime is, so unresolved lands with the
# series.
_IS_FILM = CatalogWork.format == "MOVIE"
_IS_NOT_FILM = (CatalogWork.format.is_(None)) | (CatalogWork.format != "MOVIE")

# The strict anime wall (user's call, 2026-08-09): anime rails show only
# complete cards. The denominator is the provider's — episode_count for a
# finished run, aired-so-far for an airing one — and deliberately not the
# highest episode seen, so a merged-season card (Frieren: covered 38, S1 count
# 28) is not hidden over a season the provider record does not describe. A film
# needs no episode arithmetic. Unknown-count shows are off the wall; the
# background completion sweep and the half-hourly enrichment are their way on.
_DENOM = case(
    (CatalogWork.release_status == "RELEASING", CatalogWork.episodes_aired),
    else_=CatalogWork.episode_count,
)
_ANIME_COMPLETE = or_(
    _IS_FILM,
    and_(_DENOM.isnot(None), _DENOM > 0, CatalogWork.episodes_covered >= _DENOM),
)


def _base(kind: str | None, rail: str | None = None) -> Select:
    # Adult titles never reach a rail. The provider says so — AniList
    # `isAdult`, TMDB `adult` — because the category cannot: Nyaa files adult
    # anime under TV/Anime, so classification passes it through and it landed
    # on the home page.
    q = select(CatalogWork).where(
        CatalogWork.release_count > 0, CatalogWork.adult.is_(False)
    )

    # The top-level split the pills use. The provider decides kind and format,
    # so both walls are derivable; `anime` stays valid for old links and keeps
    # its films-in-a-rail shape below.
    if kind == "anime-movies":
        return q.where(CatalogWork.kind == "anime", _IS_FILM)
    if kind == "anime-series":
        return q.where(CatalogWork.kind == "anime", _IS_NOT_FILM, _ANIME_COMPLETE)
    if kind == "all":
        # Anime rows obey the strict rule wherever they appear.
        return q.where(or_(CatalogWork.kind != "anime", _ANIME_COMPLETE))

    if kind and kind != "all":
        q = q.where(CatalogWork.kind == kind)
        if kind == "anime":
            q = q.where(_ANIME_COMPLETE)

    # Only the mixed anime wall splits on format per rail: a live-action film
    # already has its own kind, and filtering the Movies wall by MOVIE would
    # leave it showing a subset of itself.
    if kind == "anime":
        if rail == "films":
            q = q.where(_IS_FILM)
        elif rail is not None:
            q = q.where(_IS_NOT_FILM)
    return q


def _ordered(q: Select, sort: str) -> Select:
    if sort == "latest":
        # Newest first, by the indexer's own publish date. Ordering on
        # first_seen_at instead gave every work in a pass the same timestamp, so
        # the row fell back to insertion order and looked shuffled.
        return q.order_by(
            CatalogWork.latest_release_at.desc().nullslast(), CatalogWork.id.desc()
        )
    return q.order_by(
        CatalogWork.best_seeder_pct.desc(),
        CatalogWork.release_count.desc(),
        CatalogWork.id.desc(),
    )


def _seek(q: Select, sort: str, cursor: tuple[str, int] | None) -> Select:
    if cursor is None:
        return q
    value, work_id = cursor
    if sort == "latest":
        from datetime import datetime

        try:
            after = datetime.fromisoformat(value)
        except ValueError:
            return q
        return q.where(
            or_(
                CatalogWork.latest_release_at < after,
                (CatalogWork.latest_release_at == after) & (CatalogWork.id < work_id),
            )
        )
    try:
        pct = float(value)
    except ValueError:
        return q
    return q.where(
        or_(
            CatalogWork.best_seeder_pct < pct,
            (CatalogWork.best_seeder_pct == pct) & (CatalogWork.id < work_id),
        )
    )


def page(
    db: Session,
    sort: str,
    kind: str | None = None,
    cursor: str | None = None,
    exclude: set[int] | None = None,
    limit: int = PAGE,
    rail: str | None = None,
) -> tuple[list[CatalogWork], str | None]:
    """One page of a rail, plus the cursor for the next.

    `exclude` is how the mutual-exclusion rule is applied. It is filtered in
    Python rather than as a NOT IN, because the excluded set is the previous
    rails' contents and grows with each rail — a NOT IN of a few hundred ids
    per query is worse than a list comprehension over one page.
    """
    exclude = exclude or set()
    q = _seek(_ordered(_base(kind, rail), sort), sort, decode_cursor(cursor))

    # Over-fetch so exclusions do not produce a short page that looks like the
    # end of the rail.
    rows = list(db.execute(q.limit(limit + len(exclude) + 1)).scalars())
    kept = [w for w in rows if w.id not in exclude][: limit + 1]

    more = len(kept) > limit
    kept = kept[:limit]
    next_cursor = encode_cursor(kept[-1], sort) if more and kept else None
    return kept, next_cursor


def layout_for(count: int, has_more: bool) -> str:
    """How a row should render given how much it has.

    A four-item rail shows all four and 60% empty track, which reads as a
    rendering failure. A four-item grid reads as deliberate.
    """
    if count == 0:
        return "empty"
    if has_more or count >= RAIL_MINIMUM:
        return "rail"
    return "grid"
