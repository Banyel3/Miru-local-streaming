"""One refresh pass: ask the indexers what exists, keep what came back.

Deliberately not a cache fill. Rows already present are updated in place and
rows that stopped appearing are kept and aged, because the depth the wall needs
cannot come from a single snapshot — see models.py.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from miru.acquisition.provider import SearchResult
from miru.catalog.classify import classify
from miru.catalog.models import CatalogRefresh, CatalogRelease, CatalogWork
from miru.catalog.parse import normalised, parse, predict_strategy
from miru.catalog.rank import Candidate, seeder_percentiles
from miru.catalog.resolve import apply, cached, kind_from, work_by_provider
from miru.core.config import settings

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _published(raw: str | None) -> datetime | None:
    """Parse the indexer's publish date. Never raises — a release with an
    unreadable date is still a release."""
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _work_for(db: Session, kind: str, title: str, year: int | None) -> CatalogWork:
    """Find or create the work a release belongs to.

    The key is the metadata provider's id whenever this title has already been
    resolved to one — that is what makes *Youjo Senki* and *Saga of Tanya the
    Evil* one card. Resolution is read from the cache and never fetched here: a
    refresh pass must not stall behind a rate-limited API, so unknown titles
    fall back to the title grouping and are resolved by the enrichment pass that
    runs straight after.

    Without a provider the key is kind plus normalised title plus year, and when
    the year is absent it stays absent rather than being guessed: merging a
    yearless release into a dated work can attach the wrong film's releases to a
    card, and splitting only shows the same title twice.
    """
    data = cached(db, kind, title)
    if data:
        work = work_by_provider(db, kind, data)
        if work is not None:
            work.last_seen_at = _now()
            return work
        title = data.get("display_title") or title
        year = data.get("year") or year
        # And the kind, because `apply()` below sets it from the provider. Look
        # up under the indexer's kind and the row found would not be the row the
        # apply produces, so the rename collides with whatever already holds the
        # provider's identity — measured as an outright
        # `duplicate key value violates unique constraint "uq_work_identity"`
        # while re-grouping the live catalogue.
        kind = kind_from(data, kind)

    key = normalised(title)
    work = db.execute(
        select(CatalogWork).where(
            CatalogWork.kind == kind,
            CatalogWork.normalised_title == key,
            CatalogWork.year.is_(year) if year is None else CatalogWork.year == year,
        )
    ).scalar_one_or_none()

    if work is None:
        work = CatalogWork(
            kind=kind, normalised_title=key, year=year, display_title=title, genres=[]
        )
        db.add(work)
        db.flush()
    else:
        work.last_seen_at = _now()

    # An existing card under the provider's own title, seen before it was
    # resolved. Claiming it is the merge, and it also keeps the identity
    # constraint from being violated by a second row with the same name.
    if data and not work.provider:
        apply(work, data)
        db.flush()
    return work


def _upsert_release(db: Session, r: SearchResult, kind: str, pct: float) -> bool:
    """Write one release. Returns True if it is new to the catalog."""
    existing = db.execute(
        select(CatalogRelease).where(CatalogRelease.info_hash == r.info_hash)
    ).scalar_one_or_none()

    p = parse(r.title, kind)
    work = _work_for(db, kind, p.title, p.year)

    if existing is None:
        db.add(
            CatalogRelease(
                info_hash=r.info_hash,
                indexer=r.indexer,
                guid=r.magnet or r.download_url or r.id,
                title=r.title,
                kind=kind,
                work_id=work.id,
                parsed_title=p.title,
                year=p.year,
                season=p.season,
                episode=p.episode,
                episode_end=p.episode_end,
                quality=p.quality,
                release_group=p.group,
                size_bytes=int(r.size_bytes or 0),
                seeders=int(r.seeders or 0),
                leechers=int(r.leechers or 0),
                seeder_pct=pct,
                magnet=r.magnet,
                download_url=r.download_url,
                imdb_id=r.imdb_id,
                categories=list(r.categories or []),
                predicted_strategy=predict_strategy(r.title),
                published_at=_published(r.published_at),
            )
        )
        return True

    # Seen again: refresh the volatile fields and clear the staleness counter.
    existing.guid = r.magnet or r.download_url or r.id
    existing.published_at = _published(r.published_at) or existing.published_at
    existing.seeders = int(r.seeders or 0)
    existing.leechers = int(r.leechers or 0)
    existing.seeder_pct = pct
    existing.missed_refreshes = 0
    existing.last_seen_at = _now()
    existing.work_id = work.id
    return False


def _restate_works(db: Session, only: set[int] | None = None) -> None:
    """Recompute what the rails sort on.

    Done as a pass over the affected works rather than incrementally, because a
    release dropping out has to lower its work's standing too, and incremental
    maxima only ever go up.

    `only` narrows it to the works just written. A refresh restates everything
    because it also ages rows it did not see; a search touched a handful of
    works and has no business loading every release in the catalogue.
    """
    q = select(CatalogWork)
    if only is not None:
        q = q.where(CatalogWork.id.in_(only))
    for work in db.execute(q).scalars().all():
        releases = db.execute(
            select(CatalogRelease).where(CatalogRelease.work_id == work.id)
        ).scalars().all()
        # A work whose releases all moved elsewhere is a ghost: invisible on the
        # wall (rails filter on release_count) but still holding the unique key
        # the card that absorbed them needs. Deleted rather than left behind —
        # `JUJUTSU KAISEN [0]` beside `Jujutsu Kaisen [2]` was one of 21 of them.
        if not releases and not work.library_file_id and not work.download_job_id:
            db.delete(work)
            continue
        live = [r for r in releases if not r.stale and r.grabbable]
        work.release_count = len(releases)
        work.best_seeder_pct = max((r.seeder_pct for r in live), default=0.0)
        dates = [r.published_at for r in releases if r.published_at]
        work.latest_release_at = max(dates) if dates else work.first_seen_at


def _ingest(
    db: Session, results: list[SearchResult], kinds: tuple[str, ...] = ()
) -> tuple[set[str], int, dict[str, int]]:
    """classify -> parse -> upsert, for whatever the indexers just handed us.

    Returns the infohashes written, how many were new, and the per-indexer
    counts. Deliberately does no bookkeeping: aging and restating belong to
    whoever knows what the results *mean*, and a search means something
    different from a refresh pass.
    """
    # Classify first: a release we cannot place is not a release we ingest.
    placed: list[tuple[SearchResult, str]] = []
    for r in results:
        kind = classify(getattr(r, "category_ids", []) or [])
        # No infohash means no stable identity, and a row we cannot recognise
        # next time is a row that would be re-inserted forever. Measured, every
        # result from all three indexers carries one.
        if kind and (not kinds or kind in kinds) and r.grabbable and r.info_hash:
            placed.append((r, kind))

    # Percentiles are computed across the whole batch, because standing is
    # relative to what that indexer is currently showing.
    pct = seeder_percentiles(
        [
            Candidate(
                id=r.info_hash,
                title=r.title,
                indexer=r.indexer,
                seeders=int(r.seeders or 0),
                size_bytes=int(r.size_bytes or 0),
            )
            for r, _ in placed
        ]
    )

    seen: set[str] = set()
    added = 0
    per_indexer: dict[str, int] = {}

    for r, kind in placed:
        # The same torrent listed at two indexers is one torrent.
        if r.info_hash in seen:
            continue
        seen.add(r.info_hash)
        if _upsert_release(db, r, kind, pct.get(r.info_hash, 0.5)):
            added += 1
        per_indexer[r.indexer] = per_indexer.get(r.indexer, 0) + 1

    return seen, added, per_indexer


def ingest_search(db: Session, results: list[SearchResult]) -> dict:
    """Keep what a live search found.

    Searching used to render and discard, which threw away the only reach Miru
    has: each indexer's front page spans about one day, `limit` is ignored and
    `offset` returns nothing, so a query is the ONLY way to see anything older.
    Searching for a show therefore adds it to the catalogue permanently.

    Not a refresh pass, and the difference is the whole reason this is its own
    function: nothing is aged and no CatalogRefresh row is written. A query says
    what matches it, not what the indexers are currently showing, so a release
    it did not mention was never asked about rather than missing.

    Releases land with their real `published_at` — the same field a refresh
    writes — so an eight-year-old release found by searching sorts where it
    belongs instead of jumping to the top of the Latest rail.
    """
    seen, added, _ = _ingest(db, results)
    if not seen:
        return {"seen": 0, "added": 0}

    db.flush()
    work_ids = {
        wid
        for wid in db.execute(
            select(CatalogRelease.work_id).where(CatalogRelease.info_hash.in_(seen))
        ).scalars()
        if wid is not None
    }
    _restate_works(db, work_ids)
    db.commit()
    return {"seen": len(seen), "added": added}


def refresh(db: Session, provider, kinds: tuple[str, ...] = ()) -> dict:
    """Run one pass and record it.

    `provider` is anything with `.search()`; the browse query is an empty one,
    which the live instance answers with its indexers' front pages.
    """
    row = CatalogRefresh(per_indexer={})
    db.add(row)
    db.flush()

    # The empty query is the indexers' front pages; the configured ones are
    # whatever this person actually watches. Without the second, anything
    # regional is invisible on the wall however much of it exists.
    queries = [""] + [q.strip() for q in (settings.catalog_queries or "").split(",") if q.strip()]

    results: list[SearchResult] = []
    failures: list[str] = []
    for q in queries:
        try:
            results.extend(provider.search(q))
        except Exception as exc:  # noqa: BLE001 — recorded, not swallowed
            failures.append(f"{q or 'browse'}: {exc}")
            log.warning("catalog query %r failed: %s", q or "browse", exc)

    if not results:
        row.error = ("; ".join(failures) or "no results")[:512]
        row.finished_at = _now()
        db.commit()
        return {"seen": 0, "added": 0, "error": row.error}

    # A partial failure is recorded but does not discard the queries that did
    # work: one dead indexer should not empty the wall.
    if failures:
        row.error = ("; ".join(failures))[:512]

    seen, added, per_indexer = _ingest(db, results, kinds)

    # Age everything this pass did not see. Kept, never deleted.
    for rel in db.execute(select(CatalogRelease)).scalars():
        if rel.info_hash not in seen:
            rel.missed_refreshes += 1

    _restate_works(db)

    row.seen = len(seen)
    row.added = added
    row.per_indexer = per_indexer
    row.finished_at = _now()
    db.commit()

    log.info("catalog refresh: %d seen, %d new, %s", row.seen, added, per_indexer)
    return {"seen": row.seen, "added": added, "per_indexer": per_indexer}
