"""Asking an indexer for the whole series, rather than what it uploaded today.

The catalogue is built from each indexer's front page, and that page is about a
day deep — `limit` is ignored and `offset` returns nothing, both measured. So a
card holds many encodings of the few episodes uploaded this week and nothing
else. On the live catalogue:

    ONE PIECE      206 releases  ->   82 distinct episodes  of 1172
    BLACK TORCH     26 releases  ->    5 distinct episodes
    Cat and Dragon  17 releases  ->    3 distinct episodes

No ranking or filtering reaches the missing ones: they were never fetched. A
query does, immediately — `one piece batch` returns 126 results and every one is
a pack, including the whole run to episode 1071.

So opening a card asks. Once per show per day, in the background, through the
same ingest path as everything else.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from sqlalchemy import func, select as sa_select

from miru.acquisition.prowlarr import provider
from miru.acquisition.provider import AcquisitionError
from miru.catalog.ingest import ingest_search
from miru.catalog.models import CatalogRelease, CatalogWork
from miru.catalog.enrich import _loose

log = logging.getLogger(__name__)

# Both words, because they find different things. `batch` is what fansub groups
# tag a completed run with; `complete` is how a season pack is worded, and
# `spy x family complete` is the only query that surfaces the Trix BDRip at all.
TERMS = ("batch", "complete")

# The front page turns over in about a day, so asking again sooner cannot
# discover anything the last pass did not.
WINDOW = timedelta(days=1)

# A pack query is broad by nature. Kept generous because packs are what we came
# for, and `ingest_search` already refuses anything that is not video.
LIMIT = 100


def due(work: CatalogWork, now: datetime | None = None) -> bool:
    """Whether this work is worth asking about again.

    A card polls itself while it is open, so sweeping per request would fire a
    search at four indexers every couple of seconds — the same shape that had
    the live remux starting an ffmpeg per poll.
    """
    if work.kind not in ("anime", "series"):
        # A film has no missing episodes, and "batch" against one returns
        # somebody's whole filmography.
        return False
    if not (work.display_title or "").strip():
        return False
    if work.swept_at is None:
        return True
    last = work.swept_at
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (now or datetime.now(timezone.utc)) - last >= WINDOW


# At most this many names per sweep. A card open must stay a handful of
# background requests, not a fan-out per naming variant.
MAX_NAMES = 3


def names_for(db: Session, work: CatalogWork) -> list[str]:
    """The names this show actually goes by on the indexers.

    Measured live: 'Frieren: Beyond Journey's End batch' finds 13 results and
    'Sousou no Frieren batch' finds 49 — packs are named by fansub groups in
    romaji, and the canonical title alone misses most of them. The variants are
    already in the catalogue: the releases' parsed titles are the strings the
    indexers really carry. Most frequent first, near-duplicates (same
    normalised form, punctuation aside) folded together.
    """
    # _loose, not normalised: "Journey's" vs "Journeys" is one apostrophe and
    # the same show — the exact miss that cost the resolution guard 83% of its
    # matches before e2888c9.
    seen = {_loose(work.display_title)}
    names = [work.display_title]
    rows = db.execute(
        sa_select(CatalogRelease.parsed_title, func.count().label("n"))
        .where(CatalogRelease.work_id == work.id, CatalogRelease.parsed_title.isnot(None))
        .group_by(CatalogRelease.parsed_title)
        .order_by(func.count().desc())
    ).all()
    for title, _ in rows:
        if len(names) >= MAX_NAMES:
            break
        key = _loose(title)
        if key in seen or not key:
            continue
        seen.add(key)
        names.append(title)
    return names


def completion_candidates(db: Session, limit: int = 8) -> list[CatalogWork]:
    """The hidden works most worth asking about, stalest first.

    The strict wall hides fragments and unknown-count anime, and the card-open
    sweep only fires on open — nobody opens what nobody sees, so without this
    the wall stays thin forever. Selection mirrors the wall's own rule
    (rails._ANIME_COMPLETE) inverted: anything the wall would hide for
    coverage reasons is a candidate. Never-swept works come first — nothing is
    known about them — then the least recently swept.
    """
    from sqlalchemy import case as sa_case

    denom = sa_case(
        (CatalogWork.release_status == "RELEASING", CatalogWork.episodes_aired),
        else_=CatalogWork.episode_count,
    )
    incomplete = (
        denom.is_(None) | (denom <= 0) | (CatalogWork.episodes_covered < denom)
    )
    return list(
        db.execute(
            sa_select(CatalogWork)
            .where(
                CatalogWork.kind == "anime",
                (CatalogWork.format.is_(None)) | (CatalogWork.format != "MOVIE"),
                CatalogWork.release_count > 0,
                CatalogWork.adult.is_(False),
                incomplete,
            )
            .order_by(CatalogWork.swept_at.asc().nullsfirst())
            .limit(limit)
        ).scalars()
    )


def sweep_for_completion(db: Session, limit: int = 8) -> int:
    """One background pass: sweep the works the wall is hiding. Returns swept.

    Bounded — `limit` works × ≤6 searches — and self-debouncing through
    `sweep()`'s own 24 h per-work window, so a work with no packs anywhere is
    asked once a day, not once a tick.
    """
    n = 0
    for work in completion_candidates(db, limit):
        if sweep(db, work):
            n += 1
        db.commit()
    return n


def sweep(db: Session, work: CatalogWork) -> int:
    """Look for complete packs of this show. Returns how many results were seen.

    Never raises. An indexer being down is a card with fewer options on it, not
    a card that fails to open.
    """
    if not due(work):
        return 0

    title = work.display_title.strip()
    seen = 0
    for name in names_for(db, work):
        for term in TERMS:
            try:
                results = provider.search(f"{name} {term}", LIMIT)
            except AcquisitionError as exc:
                log.info("pack sweep %r (%s) found nothing: %s", name, term, exc)
                continue
            except Exception:  # noqa: BLE001 — a sweep must never break the card
                log.exception("pack sweep %r (%s) failed", name, term)
                continue

            seen += len(results)
            try:
                ingest_search(db, results)
            except Exception:  # noqa: BLE001 — nor may writing what it found
                log.exception("could not ingest pack results for %r", name)
                db.rollback()

    # Stamped even when nothing came back. A show with no packs must not be
    # searched again on every single open.
    work.swept_at = datetime.now(timezone.utc)
    log.info("pack sweep for %r saw %d results", title, seen)
    return seen
