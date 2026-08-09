"""Use case: the home page, and what must never be on it.

The category filter handles the indexers that categorise honestly — a search
for "filipino" no longer returns 129 XXX rows. It cannot help with Nyaa, which
files adult ANIME under `TV/Anime` like everything else. So `Ingoku Danchi:
Deviant's Apartment Complex` came through classification clean, was ingested,
resolved, and landed on the Trending rail of the home page with 11 releases.

No amount of category reading fixes that, because the category is not wrong —
it really is anime. The provider is the thing that knows: AniList carries
`isAdult` on every record and TMDB carries `adult`. That is the same move as
the rest of this catalogue, where identity, kind and format all come from the
provider rather than from a string.
"""

import pytest

from miru.catalog import enrich
from miru.catalog.models import CatalogWork
from miru.catalog.rails import _base


@pytest.fixture(autouse=True)
def no_throttle(monkeypatch):
    monkeypatch.setattr(enrich, "_MIN_INTERVAL", 0.0)


class TestTheProviderIsAsked:
    def test_anilist_reports_an_adult_title(self, monkeypatch):
        monkeypatch.setattr(enrich, "_get", lambda *a, **k: {
            "data": {"Media": {"id": 1, "isAdult": True, "title": {"romaji": "Ingoku Danchi"},
                               "format": "OVA", "startDate": {"year": 2026}}}})
        assert enrich._anilist("Ingoku Danchi")["adult"] is True

    def test_anilist_reports_an_ordinary_title(self, monkeypatch):
        monkeypatch.setattr(enrich, "_get", lambda *a, **k: {
            "data": {"Media": {"id": 2, "isAdult": False, "title": {"romaji": "Frieren"},
                               "format": "TV", "startDate": {"year": 2023}}}})
        assert enrich._anilist("Frieren")["adult"] is False

    def test_a_record_that_omits_the_flag_is_not_assumed_adult(self, monkeypatch):
        # Missing means unknown. Treating unknown as adult would empty the wall.
        monkeypatch.setattr(enrich, "_get", lambda *a, **k: {
            "data": {"Media": {"id": 3, "title": {"romaji": "Something"}, "format": "TV"}}})
        assert enrich._anilist("Something")["adult"] is False

    def test_tmdb_reports_its_own_adult_flag(self, monkeypatch):
        monkeypatch.setattr(enrich.settings, "tmdb_api_key", "k")
        monkeypatch.setattr(enrich, "_get", lambda *a, **k: {
            "results": [{"id": 9, "title": "Sex Trip", "adult": True, "release_date": "2026-01-01"}]})
        assert enrich._tmdb("Sex Trip", None, "movie")["adult"] is True


class TestItNeverReachesTheWall:
    def _w(self, db, title, adult, kind="series"):
        # kind=series: these tests pin the ADULT filter alone, and the strict
        # anime wall (episode-count based) would hide countless anime rows for
        # its own reason, shadowing the thing under test.
        w = CatalogWork(kind=kind, normalised_title=title.casefold(), display_title=title,
                        release_count=3, best_seeder_pct=50.0, adult=adult)
        db.add(w)
        db.commit()
        return w

    def test_an_adult_work_is_not_on_any_rail(self, db_session):
        self._w(db_session, "Ingoku Danchi", True)
        keep = self._w(db_session, "Frieren", False)
        for rail in ("latest", "trending"):
            got = db_session.execute(_base("series", rail=rail)).scalars().all()
            assert all(w.id != 1 or w.id == keep.id for w in got)
            assert "Ingoku" not in " ".join(w.display_title for w in got), rail

    def test_an_ordinary_work_is_unaffected(self, db_session):
        keep = self._w(db_session, "Frieren", False)
        got = db_session.execute(_base("series", rail="latest")).scalars().all()
        assert [w.id for w in got] == [keep.id]

    def test_a_work_nobody_has_resolved_is_still_shown(self, db_session):
        # `adult` is unknown for an unresolved work. Hiding those would empty
        # the wall of everything the providers have not answered about yet.
        w = CatalogWork(kind="series", normalised_title="unknown", display_title="Unknown",
                        release_count=1, best_seeder_pct=1.0)
        db_session.add(w)
        db_session.commit()
        got = db_session.execute(_base("series", rail="latest")).scalars().all()
        assert [x.id for x in got] == [w.id]

    def test_the_movies_wall_hides_them_too(self, db_session):
        w = CatalogWork(kind="movie", normalised_title="sex trip", display_title="Sex Trip",
                        release_count=1, best_seeder_pct=1.0, adult=True)
        db_session.add(w)
        db_session.commit()
        assert db_session.execute(_base("movie", rail="latest")).scalars().all() == []
