"""Use case: searching, and being offered things that are not films.

Measured against the live indexers on 2026-08-08 — 1650 results over eight
queries, of which 326 were not video at all:

    280  XXX (a search for "filipino" is where most of it landed)
     50  PC/Games      <- executables
     60  Books / EBook / Comics
     26  Audio         <- the VSPO voice packs

`classify()` already refuses every one of these: XXX is checked first and wins
outright, and anything outside the TV / Movies / Anime ranges returns None. It
is why the wall is clean. Search never called it — the results went from
Prowlarr to the browser untouched.

That is a safety problem and not only a tidiness one. Every row carries a
download button, so a `PC/Games` row is a one-click path to running somebody
else's executable on the machine that hosts the library.

Nothing here talks to Prowlarr; the provider is faked at the seam.
"""

import pytest

from miru.acquisition.provider import SearchResult


def _r(title, *category_ids, indexer="Knaben"):
    return SearchResult(
        id=f"magnet:?xt=urn:btih:{abs(hash(title)):040x}",
        title=title,
        indexer=indexer,
        size_bytes=1,
        seeders=1,
        leechers=0,
        age_days=1,
        magnet=f"magnet:?xt=urn:btih:{abs(hash(title)):040x}",
        download_url=None,
        info_hash=f"{abs(hash(title)):040x}"[:40],
        categories=[],
        category_ids=list(category_ids),
    )


ANIME, MOVIE, TV = 5070, 2020, 5040
XXX, GAMES, BOOKS, AUDIO = 6000, 4050, 7020, 3010


@pytest.fixture
def searching(monkeypatch):
    """Answer /search with whatever the test lists."""
    rows: list[SearchResult] = []

    class Fake:
        def search(self, q, limit=50):
            return list(rows)

    import miru.acquisition.router as mod

    monkeypatch.setattr(mod, "provider", Fake())
    monkeypatch.setattr(mod, "ingest_search", lambda *a, **k: None)
    return rows


def _titles(client, q="filipino"):
    res = client.get(f"/api/acquisition/search?q={q}")
    assert res.status_code == 200, res.text
    return [r["title"] for r in res.json()]


class TestOnlyVideoIsOffered:
    def test_porn_is_not_a_search_result(self, client, searching):
        # A search for "filipino" — the user's own use case — returned 129 XXX
        # rows on the live indexers.
        searching += [_r("A Real Film 2026 1080p", MOVIE), _r("Filipino Teenie", XXX)]
        assert _titles(client) == ["A Real Film 2026 1080p"]

    def test_a_game_is_not_a_search_result(self, client, searching):
        # The one that matters most: every row has a download button, so this
        # is one click from running someone else's executable on the host.
        searching += [_r("NARUTO X BORUTO Ultimate Ninja STORM-RUNE", GAMES),
                      _r("Naruto Shippuden - 01 [1080p]", ANIME)]
        assert _titles(client) == ["Naruto Shippuden - 01 [1080p]"]

    def test_a_book_is_not_a_search_result(self, client, searching):
        searching += [_r("One Piece (Chapters 1-721)", BOOKS),
                      _r("[SubsPlease] One Piece - 1172", ANIME)]
        assert _titles(client) == ["[SubsPlease] One Piece - 1172"]

    def test_a_voice_pack_is_not_a_search_result(self, client, searching):
        searching += [_r("VSPO! Outing Voice 2022", AUDIO), _r("VSPO! Festival 2025", TV)]
        assert _titles(client) == ["VSPO! Festival 2025"]

    def test_porn_dual_tagged_as_a_movie_is_still_refused(self, client, searching):
        # The dual-tagging measured on the live indexers cuts both ways. XXX is
        # checked first and wins outright, which is what makes this safe.
        searching += [_r("Backroom Casting Couch", XXX, MOVIE)]
        assert _titles(client) == []

    def test_an_uncategorised_result_is_refused(self, client, searching):
        # `Other` is where one indexer files its porn. Unrecognised means
        # unknown, and unknown is not something to hand a download button to.
        searching += [_r("Something With No Category")]
        assert _titles(client) == []


class TestTheThingsPeopleActuallyWantStillArrive:
    def test_films_series_and_anime_all_come_through(self, client, searching):
        searching += [_r("Monay 2026 1080p Tagalog WEB-DL", MOVIE),
                      _r("Power Book III S01E01", TV),
                      _r("[SubsPlease] Frieren - 09", ANIME)]
        assert len(_titles(client)) == 3

    def test_anime_dual_tagged_as_a_movie_still_arrives(self, client, searching):
        # Nyaa tags every anime release Movies/Other as well. Dropping those
        # would empty the anime results entirely.
        searching += [_r("[Judas] One Piece - 1172", ANIME, MOVIE)]
        assert len(_titles(client)) == 1

    def test_a_nyaa_private_category_still_arrives(self, client, searching):
        # Measured previously: Nyaa emits ids in its own private block, some
        # with no name at all.
        searching += [_r("[Erai-raws] Neko to Ryuu - 07", 131088)]
        assert len(_titles(client)) == 1
