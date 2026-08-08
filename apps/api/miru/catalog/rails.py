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

from sqlalchemy import Select, or_, select
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


RAILS = [
    Rail("trending", "Trending now", "人気", "trending"),
    Rail("fresh", "Fresh this week", "新着", "fresh"),
]


def encode_cursor(work: CatalogWork, sort: str) -> str:
    value = work.best_seeder_pct if sort == "trending" else work.first_seen_at.isoformat()
    return base64.urlsafe_b64encode(f"{value}|{work.id}".encode()).decode()


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


def _base(kind: str | None) -> Select:
    q = select(CatalogWork).where(CatalogWork.release_count > 0)
    if kind and kind != "all":
        q = q.where(CatalogWork.kind == kind)
    return q


def _ordered(q: Select, sort: str) -> Select:
    if sort == "fresh":
        return q.order_by(CatalogWork.first_seen_at.desc(), CatalogWork.id.desc())
    return q.order_by(
        CatalogWork.best_seeder_pct.desc(),
        CatalogWork.release_count.desc(),
        CatalogWork.id.desc(),
    )


def _seek(q: Select, sort: str, cursor: tuple[str, int] | None) -> Select:
    if cursor is None:
        return q
    value, work_id = cursor
    if sort == "fresh":
        from datetime import datetime

        try:
            after = datetime.fromisoformat(value)
        except ValueError:
            return q
        return q.where(
            or_(
                CatalogWork.first_seen_at < after,
                (CatalogWork.first_seen_at == after) & (CatalogWork.id < work_id),
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
) -> tuple[list[CatalogWork], str | None]:
    """One page of a rail, plus the cursor for the next.

    `exclude` is how the mutual-exclusion rule is applied. It is filtered in
    Python rather than as a NOT IN, because the excluded set is the previous
    rails' contents and grows with each rail — a NOT IN of a few hundred ids
    per query is worse than a list comprehension over one page.
    """
    exclude = exclude or set()
    q = _seek(_ordered(_base(kind), sort), sort, decode_cursor(cursor))

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
