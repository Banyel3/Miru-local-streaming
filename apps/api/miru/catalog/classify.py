"""Which of Anime / Movies / Series a release belongs in.

Prowlarr's own category filter cannot be trusted for this. Measured against the
live instance on 2026-08-08: asking for Movies (`categories=2000`) returns all
130 of Nyaa's anime releases, because Nyaa tags every one of them with *both*
`5070 TV/Anime` and `2020 Movies/Other`. Passing the user's chosen filter
through to Prowlarr would put One Piece batches in the Movies row.

So classification happens here, on ids rather than names — the same measurement
found category objects carrying an id and no name at all (`{"id": 131088}`),
so a name-based rule silently drops them.
"""

from __future__ import annotations

ANIME = "anime"
MOVIE = "movie"
SERIES = "series"

# Torznab's standard anime category, plus the private block Nyaa emits alongside
# it. The range is taken from what the live indexer actually returns rather than
# from a spec, because that block is the indexer's own numbering.
ANIME_IDS = {5070}
NYAA_PRIVATE_RANGE = (127720, 134634)

TV_RANGE = (5000, 5999)
MOVIE_RANGE = (2000, 2999)

# Checked before anything else, for the same reason anime is checked before
# movies: indexers dual-tag. An adult release tagged both 6000 XXX and 2000
# Movies is adult, and letting the Movies tag win would put it on the wall
# between two films.
XXX_RANGE = (6000, 6999)


def classify(category_ids: list[int]) -> str | None:
    """The kind a release belongs to, or None if it is not video we can place.

    Anime is checked first and wins outright. That ordering *is* the fix for
    Nyaa's dual tagging: a release tagged both anime and movie is anime, because
    the anime tag is the specific claim and the movie tag is the fallback the
    indexer emits for everything.
    """
    ids = {int(c) for c in category_ids if c is not None}
    if not ids:
        return None

    # Excluded outright and first. This is not squeamishness — it is the same
    # dual-tagging problem measured on Nyaa, and a search for "filipino"
    # returned 24 of these among 251 results.
    if any(XXX_RANGE[0] <= i <= XXX_RANGE[1] for i in ids):
        return None

    if ids & ANIME_IDS:
        return ANIME
    if any(NYAA_PRIVATE_RANGE[0] <= i <= NYAA_PRIVATE_RANGE[1] for i in ids):
        return ANIME
    if any(TV_RANGE[0] <= i <= TV_RANGE[1] for i in ids):
        return SERIES
    if any(MOVIE_RANGE[0] <= i <= MOVIE_RANGE[1] for i in ids):
        return MOVIE

    # Music, books, software, XXX. Not an error — just not the wall.
    return None
