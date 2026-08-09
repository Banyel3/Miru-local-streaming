"""Use case: searching for the film you actually mean.

`Barcelona` is a Filipino film. Searching it returned every high-seeded release
whose name merely contains the word — the results were sorted by seeders alone,
so a film literally named by the query drowned under torrents about a football
club. Relevance tiers fix the ranking; filters narrow the pile.

Nothing here talks to Prowlarr; the provider is faked at the seam.
"""

import pytest

from miru.acquisition.provider import SearchResult

ANIME, MOVIE, TV = 5070, 2020, 5040


def _r(title, seeders=10, cats=(MOVIE,), size=10 << 30):
    ih = f"{abs(hash(title)):040x}"[:40]
    return SearchResult(
        id=f"magnet:?xt=urn:btih:{ih}", title=title, indexer="Knaben",
        size_bytes=size, seeders=seeders, leechers=0, age_days=1,
        magnet=f"magnet:?xt=urn:btih:{ih}", download_url=None, info_hash=ih,
        categories=[], category_ids=list(cats),
    )


@pytest.fixture
def searching(monkeypatch):
    rows: list[SearchResult] = []

    class Fake:
        def search(self, q, limit=50):
            return list(rows)

    import miru.acquisition.router as mod

    monkeypatch.setattr(mod, "provider", Fake())
    monkeypatch.setattr(mod, "ingest_search", lambda *a, **k: None)
    return rows


def _titles(client, q, extra=""):
    res = client.get(f"/api/acquisition/search?q={q}{extra}")
    assert res.status_code == 200, res.text
    return [r["title"] for r in res.json()]


class TestTheFilmYouNamedComesFirst:
    def test_an_exact_title_outranks_a_thousand_seeder_contains_match(
        self, client, searching
    ):
        searching += [
            _r("FC Barcelona Season Review 2026 1080p", seeders=2000),
            _r("Barcelona 2016 1080p WEB-DL", seeders=3),
            _r("Vicky Cristina Barcelona 2008 720p", seeders=900),
        ]
        got = _titles(client, "Barcelona")
        assert got[0] == "Barcelona 2016 1080p WEB-DL"

    def test_a_prefix_match_outranks_a_mid_title_word(self, client, searching):
        searching += [
            _r("Vicky Cristina Barcelona 2008 720p", seeders=900),
            _r("Barcelona Nights S01E01 720p", seeders=5, cats=(TV,)),
        ]
        got = _titles(client, "Barcelona")
        assert got[0] == "Barcelona Nights S01E01 720p"

    def test_within_a_tier_seeders_still_decide(self, client, searching):
        searching += [
            _r("Barcelona 2016 1080p WEB-DL", seeders=3),
            _r("Barcelona 2016 720p WEB-DL", seeders=80),
        ]
        got = _titles(client, "Barcelona")
        assert got[0] == "Barcelona 2016 720p WEB-DL"

    def test_punctuation_does_not_break_an_exact_match(self, client, searching):
        searching += [
            _r("Spider Man: Brand New Day 2026 1080p", seeders=1),
            _r("The Amazing Spider-Man Collection 2160p", seeders=500),
        ]
        got = _titles(client, "spider man brand new day")
        assert got[0].startswith("Spider Man: Brand New Day")


class TestFiltersNarrowThePile:
    def test_kind_filters_by_classification(self, client, searching):
        searching += [
            _r("Barcelona 2016 1080p", cats=(MOVIE,)),
            _r("Barcelona Nights S01E01", cats=(TV,)),
        ]
        assert _titles(client, "Barcelona", "&kind=movie") == ["Barcelona 2016 1080p"]

    def test_quality_filters_by_parsed_resolution(self, client, searching):
        searching += [
            _r("Barcelona 2016 1080p", seeders=5),
            _r("Barcelona 2016 720p", seeders=50),
        ]
        assert _titles(client, "Barcelona", "&quality=1080p") == ["Barcelona 2016 1080p"]

    def test_max_size_filters_out_the_disk_eaters(self, client, searching):
        searching += [
            _r("Barcelona 2016 REMUX", size=60 << 30),
            _r("Barcelona 2016 1080p", size=2 << 30),
        ]
        assert _titles(client, "Barcelona", "&max_size_gb=10") == ["Barcelona 2016 1080p"]

    def test_the_catalogue_still_learns_from_everything(self, client, searching, monkeypatch):
        # A filter is a view, not a verdict: ingest keeps seeing the whole
        # answer, or filtering a search would quietly shrink the catalogue.
        import miru.acquisition.router as mod

        seen = []
        monkeypatch.setattr(mod, "ingest_search", lambda db, rows: seen.extend(rows))
        searching += [
            _r("Barcelona 2016 1080p", cats=(MOVIE,)),
            _r("Barcelona Nights S01E01", cats=(TV,)),
        ]
        _titles(client, "Barcelona", "&kind=movie")
        assert len(seen) == 2
