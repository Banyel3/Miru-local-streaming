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
  it runs inside the ingest pass. A refresh must not stall behind ninety
  requests a minute.
- `resolve()` fills that table, one distinct title at a time, from the
  enrichment pass that already runs after ingest and is already bounded.

A title that resolves to nothing keeps grouping by title, which is exactly
today's behaviour, so nothing regresses.
"""

from __future__ import annotations

import logging
import threading
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from miru.catalog.models import CatalogWork, TitleResolution
from miru.catalog.parse import normalised

log = logging.getLogger(__name__)

# AniList's published limit is 90 requests a minute. Held below it rather than
# at it: going over returns 429 for the rest of the minute, and the pass that
# trips it is the pass that resolves nothing.
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


def _widen(kind: str, title: str, year: int | None) -> dict | None:
    """Try again with the tail of the title dropped, a word at a time.

    Release names carry tails no provider has heard of — "NCOP+NCED",
    "Tagalog", "S01E1172", "1985 LDRip". Measured: 33 of 135 distinct anime
    titles missed on the full string, and shortening recovers 15 of them,
    *Ore Monogatari!!* and *One Piece* among them.
    """
    import miru.catalog.enrich as enrich

    words = title.split()
    for n in range(len(words) - 1, 0, -1):
        _throttle()
        data = enrich.lookup(kind, " ".join(words[:n]), year)
        if data is None:
            continue
        # Accepted only if the provider's own name is really in the release
        # title. Without this, "Detective Conan Movie 2 The Fourteenth Target"
        # shortens to a *different* Conan film — a merged card offering the
        # wrong download, which parse.py's rule says is worse than a split one.
        got = normalised(data.get("display_title") or "")
        return data if got and got in normalised(title) else None
    return None


def _row(db: Session, kind: str, key: str) -> TitleResolution | None:
    return db.execute(
        select(TitleResolution).where(
            TitleResolution.kind == kind, TitleResolution.query == key
        )
    ).scalar_one_or_none()


def cached(db: Session, kind: str, title: str) -> dict | None:
    """What is already known about this title. Database only, never the network."""
    row = _row(db, kind, normalised(title))
    return row.data if row is not None and row.provider else None


def resolve(db: Session, kind: str, title: str, year: int | None) -> dict | None:
    """Resolve a title, asking the provider at most once ever.

    Both outcomes are written down. A miss is worth remembering: the releases
    that defeat the parser are the same ones every pass.
    """
    key = normalised(title)
    row = _row(db, kind, key)
    if row is not None:
        return row.data if row.provider else None

    # Imported here, and by module rather than by name, so the enrichment
    # fetchers stay monkeypatchable and so enrich.py can call back into this.
    import miru.catalog.enrich as enrich

    _throttle()
    data = enrich.lookup(kind, title, year) or _widen(kind, title, year)
    db.add(
        TitleResolution(
            kind=kind,
            query=key,
            provider=(data or {}).get("provider"),
            provider_id=(data or {}).get("provider_id"),
            data=data or {},
        )
    )
    db.flush()
    return data


def work_by_provider(db: Session, kind: str, data: dict) -> CatalogWork | None:
    """The card this provider id already has, if any."""
    if not data.get("provider") or not data.get("provider_id"):
        return None
    return db.execute(
        select(CatalogWork).where(
            CatalogWork.kind == kind,
            CatalogWork.provider == data["provider"],
            CatalogWork.provider_id == data["provider_id"],
        )
    ).scalar_one_or_none()


def apply(work: CatalogWork, data: dict) -> None:
    """Copy a resolution onto a work — identity included.

    `normalised_title` moves with `display_title` on purpose. Setting only the
    display name is what produced a card reading *Saga of Tanya the Evil* while
    still grouping as `youjo senki`, so the next release under the provider's
    own name made a second, identical-looking card.
    """
    work.provider = data["provider"]
    work.provider_id = data["provider_id"]
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
