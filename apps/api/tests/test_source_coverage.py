"""Use case: getting more of the right sources, and fewer of the wrong ones.

Two failures, both reported against the live instance.

**A search told Miru nothing.** The indexers' front page spans about one day,
`limit` is ignored and `offset` returns nothing — so a query is the only reach
past that window, and its results were rendered once and discarded.

**One show arrived from three different groups.** Episode 1 from SubsPlease, 2
from Erai-raws, 3 from ToonsHub: three subtitle styles and three encodes in one
series, because the default pick had no memory of where the last episode came
from.
"""

import dataclasses

import pytest
from sqlalchemy import select
from tests.test_catalog_ingest import NYAA_CATS, FakeProvider, result

from miru.catalog.ingest import refresh
from miru.catalog.models import CatalogRelease, CatalogWork


class FakeIndexers:
    """Whatever Prowlarr would have answered, without Prowlarr."""

    def __init__(self, results):
        self.results = results
        self.queries = []

    def search(self, q, limit=50):
        self.queries.append((q, limit))
        return self.results


@pytest.fixture
def indexers(monkeypatch):
    def install(results):
        fake = FakeIndexers(results)
        monkeypatch.setattr("miru.acquisition.router.provider", fake)
        return fake

    return install


FRUITS = [
    result("[SubsPlease] Fruits Basket - 02 (1080p) [F00D1234]", seeders=90),
    result("[SubsPlease] Fruits Basket - 03 (1080p) [F00D5678]", seeders=80),
]


class TestASearchIsAlsoAnIngest:
    def test_searching_for_a_show_adds_it_to_the_catalogue(self, client, db_session, indexers):
        # The failure this prevents: searching for Fruits Basket, seeing it, and
        # finding the wall still empty of it afterwards — with no way to ask the
        # indexers for it again once it falls off their front page.
        indexers(FRUITS)

        body = client.get("/api/acquisition/search", params={"q": "fruits basket"}).json()

        assert len(body) == 2  # the user still gets what they searched for
        works = db_session.execute(select(CatalogWork)).scalars().all()
        assert [w.display_title for w in works] == ["Fruits Basket"]
        assert works[0].release_count == 2  # restated, or the rails never show it

    def test_an_old_release_does_not_jump_to_the_top_of_latest(
        self, client, db_session, indexers
    ):
        # Searching reaches *behind* the one-day window, so most of what it
        # finds is old. Ingesting it with the time of the search would push a
        # 2018 release to the top of the Latest rail and make search results
        # look like new uploads.
        indexers([dataclasses.replace(r, published_at="2018-04-05T12:00:00Z") for r in FRUITS])

        client.get("/api/acquisition/search", params={"q": "fruits basket"})

        work = db_session.execute(select(CatalogWork)).scalars().one()
        assert work.latest_release_at.year == 2018

    def test_a_search_does_not_age_the_rest_of_the_catalogue(
        self, client, db_session, indexers
    ):
        # A refresh pass surveys what the indexers are showing, so anything
        # missing from it is missing. A query only says what matches it. Running
        # a search through the pass logic would have aged every unrelated row
        # towards stale — twenty searches and the whole wall stops being
        # recommendable.
        refresh(db_session, FakeProvider([[result("[Grp] Other Show - 01 [1080p]")]]))
        indexers(FRUITS)

        client.get("/api/acquisition/search", params={"q": "fruits basket"})

        other = db_session.execute(
            select(CatalogRelease).where(CatalogRelease.title.like("%Other Show%"))
        ).scalar_one()
        assert other.missed_refreshes == 0

    def test_a_one_character_query_writes_nothing(self, client, db_session, indexers):
        indexers(FRUITS)
        assert client.get("/api/acquisition/search", params={"q": "f"}).status_code == 422
        assert db_session.execute(select(CatalogRelease)).scalars().all() == []

    def test_results_with_no_infohash_are_shown_but_not_stored(
        self, client, db_session, indexers
    ):
        # No infohash is no stable identity: stored, it would be re-inserted on
        # every pass forever.
        indexers([dataclasses.replace(FRUITS[0], info_hash=None, magnet=None, download_url=None)])

        body = client.get("/api/acquisition/search", params={"q": "fruits basket"}).json()

        assert len(body) == 1
        assert db_session.execute(select(CatalogRelease)).scalars().all() == []

    def test_a_dead_indexer_is_still_an_error_not_an_empty_grid(self, client, monkeypatch):
        from miru.acquisition.provider import AcquisitionError

        class Down:
            def search(self, q, limit=50):
                raise AcquisitionError("prowlarr unreachable")

        monkeypatch.setattr("miru.acquisition.router.provider", Down())
        assert client.get("/api/acquisition/search", params={"q": "fruits"}).status_code == 502


class TestOneSourcePerSeries:
    """The reported problem, through the endpoint the sheet actually calls."""

    @pytest.fixture
    def two_groups(self, db_session):
        # Same episode, same quality, same codec — so nothing but the group can
        # decide. Erai-raws is the smaller file, which is what wins the existing
        # tie-break, so an unchanged picker would flip sources mid-series.
        refresh(
            db_session,
            FakeProvider(
                [
                    [
                        result(
                            "[SubsPlease] Fruits Basket - 03 (1080p) [x264]",
                            seeders=90,
                            cats=NYAA_CATS,
                            size=1_400_000_000,
                        ),
                        result(
                            "[Erai-raws] Fruits Basket - 03 (1080p) [x264]",
                            seeders=90,
                            cats=NYAA_CATS,
                            size=900_000_000,
                        ),
                    ]
                ]
            ),
        )
        return db_session.execute(select(CatalogWork)).scalars().one()

    def _best(self, client, work_id):
        return client.get(f"/api/catalog/works/{work_id}").json()["choices"]["best"]

    def test_with_nothing_downloaded_the_usual_rules_decide(self, client, two_groups):
        assert self._best(client, two_groups.id)["group"] == "Erai-raws"

    def test_the_next_episode_comes_from_the_group_already_downloaded(
        self, client, db_session, two_groups
    ):
        # The failure this prevents: one show accumulating in the library from
        # three different groups, each with its own subtitle style and naming.
        subsplease = db_session.execute(
            select(CatalogRelease).where(CatalogRelease.release_group == "SubsPlease")
        ).scalar_one()
        # The job id IS the infohash — that is what records which release was
        # grabbed, and the whole affinity hangs off it.
        two_groups.download_job_id = subsplease.info_hash
        db_session.commit()

        assert self._best(client, two_groups.id)["group"] == "SubsPlease"

    def test_the_other_groups_are_still_listed(self, client, db_session, two_groups):
        subsplease = db_session.execute(
            select(CatalogRelease).where(CatalogRelease.release_group == "SubsPlease")
        ).scalar_one()
        two_groups.download_job_id = subsplease.info_hash
        db_session.commit()

        body = client.get(f"/api/catalog/works/{two_groups.id}").json()
        # Preferring a source is a recommendation, never a filter: the user has
        # to be able to leave a group that started subbing badly.
        assert {r["group"] for r in body["releases"]} == {"SubsPlease", "Erai-raws"}


class TestTheFrontPageIsAskedPerCategory:
    """One empty query returns ~366 rows across four indexers — and that is the
    whole window, because indexers return a front page PER CATEGORY and we only
    ever asked for the mixed one. Asking per category multiplies coverage for
    three extra requests per pass. Measured need: the ten-hour front-page
    window in docs/plans/2026-08-08-player-and-coverage.md §2.
    """

    class Recorder:
        def __init__(self):
            self.calls = []

        def search(self, query, limit=50, categories=None):
            self.calls.append((query, tuple(categories or ())))
            return []

    def test_the_browse_pass_covers_each_category_block(self, db_session):
        from miru.catalog.ingest import refresh

        p = self.Recorder()
        refresh(db_session, p)
        browse = [cats for q, cats in p.calls if q == ""]
        # The mixed front page, then one per block: anime, TV, movies.
        assert (5070,) in browse
        assert (5000,) in browse
        assert (2000,) in browse

    def test_configured_queries_are_not_multiplied_by_category(self, db_session):
        # "filipino" is one question, not three: a text query already reaches
        # past the front page, which is the only thing categories widen.
        from miru.catalog.ingest import refresh
        from miru.core.config import settings

        p = self.Recorder()
        old = settings.catalog_queries
        settings.catalog_queries = "filipino"
        try:
            refresh(db_session, p)
        finally:
            settings.catalog_queries = old
        assert [c for q, c in p.calls if q == "filipino"] == [()]

    def test_a_provider_without_the_parameter_still_works(self, db_session):
        # The worker's fake providers and any older backend take (query, limit)
        # only. A refresh must not TypeError its way into an empty wall.
        from miru.catalog.ingest import refresh

        calls = []

        class Legacy:
            def search(self, query, limit=50):
                calls.append(query)
                return []

        refresh(db_session, Legacy())
        assert "" in calls
