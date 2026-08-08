#!/usr/bin/env python
"""Resolve the whole catalogue to provider ids, once.

The enrichment pass does forty works every thirty minutes, which is right for
steady state and useless for the first run over a catalogue that already exists.
This is the same code with the cap taken off. Rate limited by resolve.py, so it
takes about a minute per seventy-five works.

    scripts/resolve-catalog.py

Every work is cleared of its `provider` mark first, because the works enriched
before identity existed carry a provider id that was never used for grouping —
they are exactly the duplicated cards this is meant to merge. Their ids are
kept, so the first one to be re-resolved becomes the card the rest fold into.

Cheap to re-run: the second pass answers from the resolution table and makes no
requests at all.
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from sqlalchemy import func, select, update  # noqa: E402

import miru.library.models  # noqa: E402, F401  (catalog_works points at media_files)
from miru.catalog.enrich import backfill  # noqa: E402
from miru.catalog.models import CatalogWork  # noqa: E402
from miru.core.db import SessionLocal  # noqa: E402


def counts(db) -> dict:
    return {
        kind: n
        for kind, n in db.execute(
            select(CatalogWork.kind, func.count()).group_by(CatalogWork.kind)
        )
    }


def main() -> None:
    started = time.monotonic()
    with SessionLocal() as db:
        before = counts(db)
        print("before:", before, "total", sum(before.values()))

        db.execute(update(CatalogWork).values(provider=None))
        db.commit()

        while True:
            out = backfill(db, limit=50)
            if not out["attempted"]:
                break
            print(f"  resolved {out['found']}/{out['attempted']}", flush=True)

        after = counts(db)
        print("after: ", after, "total", sum(after.values()))
    print(f"{time.monotonic() - started:.0f}s")


if __name__ == "__main__":
    main()
