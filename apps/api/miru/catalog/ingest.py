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
from miru.core.config import settings

log = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _work_for(db: Session, kind: str, title: str, year: int | None) -> CatalogWork:
    """Find or create the work a release belongs to.

    Grouping key is kind plus normalised title plus year. When the year is
    absent it stays absent rather than being guessed: merging a yearless release
    into a dated work can attach the wrong film's releases to a card, and
    splitting only shows the same title twice.
    """
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
            )
        )
        return True

    # Seen again: refresh the volatile fields and clear the staleness counter.
    existing.guid = r.magnet or r.download_url or r.id
    existing.seeders = int(r.seeders or 0)
    existing.leechers = int(r.leechers or 0)
    existing.seeder_pct = pct
    existing.missed_refreshes = 0
    existing.last_seen_at = _now()
    existing.work_id = work.id
    return False


def _restate_works(db: Session) -> None:
    """Recompute what the rails sort on.

    Done as a pass over the affected works rather than incrementally, because a
    release dropping out has to lower its work's standing too, and incremental
    maxima only ever go up.
    """
    for work in db.execute(select(CatalogWork)).scalars():
        releases = db.execute(
            select(CatalogRelease).where(CatalogRelease.work_id == work.id)
        ).scalars().all()
        live = [r for r in releases if not r.stale and r.grabbable]
        work.release_count = len(releases)
        work.best_seeder_pct = max((r.seeder_pct for r in live), default=0.0)


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

    # Classify first: a release we cannot place is not a release we ingest.
    placed: list[tuple[SearchResult, str]] = []
    for r in results:
        kind = classify(getattr(r, "category_ids", []) or [])
        # No infohash means no stable identity, and a row we cannot recognise
        # next time is a row that would be re-inserted forever. Measured, every
        # result from all three indexers carries one.
        if kind and (not kinds or kind in kinds) and r.grabbable and r.info_hash:
            placed.append((r, kind))

    # Percentiles are computed across the whole pass, because standing is
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
