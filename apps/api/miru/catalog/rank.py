"""Ranking releases, and picking one on the user's behalf.

Two problems, both measured rather than assumed.

**Seeders are not comparable across indexers.** On one snapshot of the live
instance:

    Nyaa.si          130 results   124 report seeders   max 303   median 10.5
    YTS              101 results    35 report seeders   max 100   median 0
    The Pirate Bay     4 results     4 report seeders   max   2   median 1

YTS leaves the field at zero for two thirds of its catalogue and The Pirate
Bay's ceiling is 2. Sorting a mixed wall by raw seeders therefore returns Nyaa,
all of it, every time — the Movies rail would be anime and the failure would be
very hard to diagnose. Rank on a release's standing *within its own indexer*
instead.

**Nobody can rank forty releases by hand.** So the picker offers three named
choices and this module computes them.
"""

from __future__ import annotations

from dataclasses import dataclass

from miru.catalog.parse import JUNK, needs_pc

# Below this, a torrent is a coin flip rather than a download.
VIABLE_SEEDERS = 5

# Below this many results from one indexer, its ordering says nothing.
MIN_SAMPLE = 5

# Preference order when the user has not asked for anything specific. 1080p
# first because it is the quality/size knee for a single viewer; 2160p last
# because it is many times the bytes and always needs the GPU.
QUALITY_PREFERENCE = ["1080p", "720p", "2160p", "576p", "480p", "360p"]


@dataclass
class Candidate:
    """Whatever the caller holds, reduced to what ranking needs."""

    id: str
    title: str
    indexer: str
    seeders: int
    size_bytes: int
    quality: str | None = None
    group: str | None = None
    grabbable: bool = True
    stale: bool = False
    # What this release covers. A card is opened to be completed, so the picker
    # has to be able to tell one episode from the whole run — it could not, and
    # on a card holding a 1-915 pack it still offered episode 1172.
    episode: int | None = None
    episode_end: int | None = None
    complete: bool = False


def seeder_percentiles(items: list[Candidate]) -> dict[str, float]:
    """Each release's standing among its own indexer's current results, 0..1.

    An indexer that reports nothing usable (every count zero) gets a flat 0.5 —
    neither promoted nor buried. Inventing a ranking from absent data would be
    worse than admitting there isn't one.
    """
    by_indexer: dict[str, list[Candidate]] = {}
    for c in items:
        by_indexer.setdefault(c.indexer, []).append(c)

    out: dict[str, float] = {}
    for group in by_indexer.values():
        counts = sorted(c.seeders for c in group)
        top = counts[-1] if counts else 0
        for c in group:
            # A percentile over one or two results is not a ranking, it is an
            # accident: a single 1-seeder result would score 1.0 and top the
            # Trending rail. Narrow regional queries produce exactly that.
            if top <= 0 or len(counts) < MIN_SAMPLE:
                out[c.id] = 0.5
            else:
                # Fraction of this indexer's releases this one is at least as
                # good as. Ties share the higher standing.
                at_or_below = sum(1 for n in counts if n <= c.seeders)
                out[c.id] = at_or_below / len(counts)
    return out


def _quality_rank(q: str | None) -> int:
    try:
        return QUALITY_PREFERENCE.index(q or "")
    except ValueError:
        return len(QUALITY_PREFERENCE)


def usable(items: list[Candidate]) -> list[Candidate]:
    """Everything the picker is allowed to offer as a default.

    Junk rips and dead or stale entries are excluded here rather than sorted
    down, because a default is a recommendation and recommending a corpse is
    worse than offering nothing.
    """
    return [
        c
        for c in items
        if c.grabbable and not c.stale and c.seeders > 0 and not JUNK.search(c.title)
    ]


def pick_default(items: list[Candidate], prefer_group: str | None = None) -> Candidate | None:
    """The release Miru grabs when the user just says "get it".

    Order of preference, most to least important:
      1. viable seeder count — a download that does not finish is not a choice
      2. preferred quality
      3. does not need the PC awake — the thing only Miru can know
      4. the group this show is already being collected from
      5. best standing within its own indexer

    Quality outranks PC-avoidance deliberately, and getting this backwards was
    a real bug: ranking on needs_pc first picked a 480p rip over a 1080p one to
    spare a GPU that exists precisely to be used. Avoiding the PC is a
    tie-breaker *within* a quality tier, not a reason to drop two tiers.

    `prefer_group` is the group of a release of this work that has already been
    downloaded, and it only breaks ties. Episode 1 from SubsPlease, 2 from
    Erai-raws and 3 from ToonsHub is one series with three subtitle styles,
    three naming conventions and three encodes — so once a show has a source,
    later episodes stay with it. It is a *recommendation* only: nothing is
    hidden, every group still appears in the full table, and the user picking
    another release is unaffected.
    """
    pool = usable(items)
    if not pool:
        return None

    pct = seeder_percentiles(pool)
    healthy = [c for c in pool if c.seeders >= VIABLE_SEEDERS]
    ranked = healthy or pool
    wanted = (prefer_group or "").casefold()

    return min(
        ranked,
        key=lambda c: (
            # Completeness first, and above quality on purpose: a complete 720p
            # run is a series you can watch and a 1080p episode is not. Smallest
            # among the complete ones, because 630 GB against 860 GB free is
            # most of the disk in one click and a 42 GB season is just as
            # complete. Seeders still gate it — an unseeded pack is not a way to
            # finish a series, it is a download that never finishes.
            not c.complete,
            # Uploaders tag any multi-episode release a batch, so completeness
            # alone picked the smallest self-declared one — live, that made the
            # ONE PIECE default `Episodes 838-875`, a chunk out of the middle.
            # A card is opened to be completed, so a run from the beginning wins
            # first; a pack that states no episodes at all is a season pack and
            # counts as one.
            c.complete and c.episode not in (1, None),
            c.size_bytes if c.complete else 0,
            _quality_rank(c.quality),
            needs_pc(c.title),
            bool(wanted) and (c.group or "").casefold() != wanted,
            -pct[c.id],
            c.size_bytes,
        ),
    )


def three_choices(
    items: list[Candidate], prefer_group: str | None = None
) -> dict[str, Candidate | None]:
    """Best / Smallest / Best quality — the whole picker.

    Each may be None, and any two may be the same release; the UI collapses
    duplicates rather than showing the same row twice under different names.

    Only "best" honours `prefer_group`: Smallest and Best quality are answers to
    questions the user asked in those words, and quietly returning a different
    group's release for them would not be answering the question.
    """
    pool = usable(items)
    if not pool:
        return {"best": None, "smallest": None, "best_quality": None}

    return {
        "best": pick_default(pool, prefer_group),
        "smallest": min(pool, key=lambda c: (c.size_bytes or 1 << 62)),
        # Highest rung available, best-seeded at that rung. Not necessarily 4K —
        # offering "Best quality" that is the same 1080p as "Best" is honest;
        # inventing a 4K row that does not exist is not.
        "best_quality": min(pool, key=lambda c: (_quality_index_desc(c.quality), -c.seeders)),
    }


_DESC = ["2160p", "1080p", "720p", "576p", "480p", "360p"]


def _quality_index_desc(q: str | None) -> int:
    try:
        return _DESC.index(q or "")
    except ValueError:
        return len(_DESC)


def all_viable_dead(items: list[Candidate]) -> bool:
    """True when nothing on offer clears the viability bar.

    The sheet says so up front in that case, rather than presenting a corpse as
    a recommendation.
    """
    pool = usable(items)
    return bool(items) and not any(c.seeders >= VIABLE_SEEDERS for c in pool)
