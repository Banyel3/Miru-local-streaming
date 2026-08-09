"""Parsed title → a metadata provider's id.

The wall groups releases into cards, and it used to group them on the parsed
title. Measured on the live catalogue that produces 111 anime works of which 71
hold exactly one release, and 21 title prefixes covering 47 works that are
really the same shows under different names:

    "Youjo Senki 幼女戦記"  /  "Saga of Tanya the Evil"  /  "Youjo Senki 幼女戦記 Movie"
    "Ore Monogatari"        /  "My Love Story!!"

Those share no characters, so string cleaning cannot merge them. AniList can,
and does — all of the first three are 21613, both of the last two are 20946. So
a release's identity is the provider id it resolves to, and the parsed title is
only ever a search term.

Two entry points, deliberately different:

- `cached()` reads the resolution table and never touches the network, because
  it runs inside the ingest pass. A refresh must not stall behind a provider
  that answers 75 times a minute.
- `resolve()` fills that table, one distinct title at a time, from the
  enrichment pass that already runs after ingest and is already bounded.

One lookup per distinct *title*, not per release: 193 anime releases carry well
under a hundred names, and new episodes of a known show cost nothing at all.

A title that resolves to nothing keeps grouping by title, which is exactly
today's behaviour, so nothing regresses.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from miru.catalog.models import CatalogWork, TitleResolution
from miru.catalog.parse import normalised

log = logging.getLogger(__name__)


def _row(db: Session, key: str) -> TitleResolution | None:
    """The cached answer for a title.

    Keyed on the title alone, not on (kind, title). The kind comes from the
    indexer's categories, and the indexers disagree: The Pirate Bay carries no
    anime tag at all, so it called Frieren a series while Nyaa called it anime.
    Keying on it asked the identical question twice and let it be answered
    differently each time — which is how one show ended up holding two provider
    ids that could never merge.
    """
    # `.first()` on an ordered query rather than one-row-or-none: the table was
    # written under the old (kind, query) key and the live database holds 15
    # titles cached twice. A resolved row is ordered ahead of an unresolved one
    # so a miss recorded under the kind that could not answer — series asking
    # TVmaze about an anime — never masks the hit recorded under the one that
    # could.
    return db.execute(
        select(TitleResolution)
        .where(TitleResolution.query == key)
        .order_by(TitleResolution.provider.is_(None), TitleResolution.id)
    ).scalars().first()


def cached(db: Session, kind: str, title: str) -> dict | None:
    """What is already known about this title. Database only, never the network."""
    row = _row(db, normalised(title))
    return row.data if row is not None and row.provider else None


def resolve(db: Session, kind: str, title: str, year: int | None) -> dict | None:
    """Resolve a title, asking the provider at most once ever.

    Both outcomes are written down. A miss is worth remembering: the releases
    that defeat the parser are the same ones every pass.
    """
    key = normalised(title)
    row = _row(db, key)
    if row is not None:
        return row.data if row.provider else None

    # Imported here, and by module rather than by name, so the enrichment
    # fetchers stay monkeypatchable and so enrich.py can call back into this.
    import miru.catalog.enrich as enrich

    data = enrich.lookup(kind, title, year)
    try:
        # In its own savepoint: this row is a cache, and two works whose titles
        # normalise the same — the same film with and without its year — must
        # not lose their enrichment to a duplicate key on it.
        with db.begin_nested():
            db.add(
                TitleResolution(
                    kind=kind,
                    query=key,
                    provider=(data or {}).get("provider"),
                    provider_id=(data or {}).get("provider_id"),
                    data=data or {},
                )
            )
    except IntegrityError:
        log.debug("resolution for %r was already written", key)
    return data


def work_by_provider(db: Session, kind: str, data: dict) -> CatalogWork | None:
    """The card this provider id already has, if any.

    `kind` is accepted and ignored. A provider id is already globally unique
    within its provider, and matching on kind as well is what kept
    anilist/154587 and tvmaze/69956 apart when they are one show that two
    indexers happened to categorise differently.
    """
    if not data.get("provider") or not data.get("provider_id"):
        return None
    return db.execute(
        select(CatalogWork).where(
            CatalogWork.provider == data["provider"],
            CatalogWork.provider_id == data["provider_id"],
        )
    ).scalar_one_or_none()


# What each provider's answer says the show actually is. AniList holds anime and
# nothing else, so a match there is a claim about the show rather than about the
# indexer that happened to carry the release. TVmaze holds television. TMDB
# holds both and says which in `format`.
_KIND_OF = {"anilist": "anime", "tvmaze": "series"}


def kind_from(data: dict, fallback: str) -> str:
    """The kind the provider implies, or the indexer's guess if it implies none."""
    known = _KIND_OF.get(data.get("provider") or "")
    if known:
        return known
    if data.get("provider") == "tmdb":
        return "movie" if (data.get("format") or "").upper() == "MOVIE" else fallback
    return fallback


def apply(work: CatalogWork, data: dict) -> None:
    """Copy a resolution onto a work — identity included.

    `normalised_title` moves with `display_title` on purpose. Setting only the
    display name is what produced a card reading *Saga of Tanya the Evil* while
    still grouping as `youjo senki`, so the next release under the provider's
    own name made a second, identical-looking card.
    """
    work.provider = data["provider"]
    work.provider_id = data["provider_id"]
    # The provider decides what this is. An anime film keeps kind=anime and is
    # told from the weekly shows by `format`, which is why the wall needs no
    # fifth pill — see docs/plans/2026-08-08-series-identity.md §7.
    work.kind = kind_from(data, work.kind)
    if data.get("adult") is not None:
        work.adult = bool(data["adult"])
    if data.get("display_title"):
        work.display_title = data["display_title"]
        work.normalised_title = normalised(data["display_title"])
    if data.get("year"):
        work.year = data["year"]
    work.poster_url = data.get("poster_url") or work.poster_url
    work.backdrop_url = data.get("backdrop_url") or work.backdrop_url
    work.overview = data.get("overview") or work.overview
    work.score = data.get("score") if data.get("score") is not None else work.score
    work.genres = data.get("genres") or work.genres or []
    work.format = data.get("format") or work.format
    work.episode_count = data.get("episode_count") or work.episode_count
    work.release_status = data.get("release_status") or work.release_status
    if data.get("episodes_aired") is not None:
        work.episodes_aired = data["episodes_aired"]
