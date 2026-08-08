"""Use case: filling the wall, and keeping it filled.

Runs the whole refresh pass against a fake provider carrying the shapes the live
indexers actually return. No network, no Prowlarr, no PC.
"""

import hashlib

import pytest
from sqlalchemy import select

from miru.acquisition.provider import SearchResult
from miru.catalog.ingest import refresh
from miru.catalog.models import CatalogRefresh, CatalogRelease, CatalogWork

NYAA_CATS = [5070, 131088, 2020]   # measured: anime is tagged as Movies too
YTS_CATS = [2040, 100045]
TPB_TV_CATS = [5030]


def result(title, indexer="Nyaa.si", seeders=100, cats=None, size=1_400_000_000, guid=None,
           info_hash=None):
    # The link is deliberately unstable, mirroring the real thing: Prowlarr
    # re-encrypts it on every response, so a test that reuses one would prove
    # something the live instance does not do. Identity comes from the
    # infohash, which is derived from the title here so it stays stable.
    magnet = guid or f"magnet:?xt=urn:btih:{abs(hash(title)) & 0xFFFFFFFF:08x}-{id(object()):x}"
    ih = info_hash or hashlib.sha1(title.encode()).hexdigest()
    return SearchResult(
        id=magnet,
        info_hash=ih,
        title=title,
        indexer=indexer,
        size_bytes=size,
        seeders=seeders,
        leechers=1,
        age_days=2,
        magnet=magnet,
        download_url=None,
        categories=["TV/Anime"],
        category_ids=cats if cats is not None else NYAA_CATS,
        imdb_id=None,
    )


class FakeProvider:
    def __init__(self, batches):
        self.batches = list(batches)
        self.calls = 0

    def search(self, query="", limit=50):
        self.calls += 1
        if isinstance(self.batches[0], Exception):
            raise self.batches[0]
        return self.batches[min(self.calls - 1, len(self.batches) - 1)]


ONE_PIECE = [
    result("[RLSP] One Piece 744-746 [BD 720p]", seeders=303),
    result("[RLSP] One Piece 721-724 [BD 720p]", seeders=293),
    result("[RLSP] One Piece 729-732 [BD 720p]", seeders=279),
]


class TestGroupingReleasesIntoWorks:
    def test_four_batches_of_one_show_become_one_card(self, db_session):
        # The reason the wall exists in this shape at all. Without grouping the
        # top of the anime rail is four cards of One Piece.
        refresh(db_session, FakeProvider([ONE_PIECE]))

        works = db_session.execute(select(CatalogWork)).scalars().all()
        assert len(works) == 1
        assert works[0].display_title == "One Piece"
        assert works[0].kind == "anime"
        assert works[0].release_count == 3

    def test_different_shows_stay_separate(self, db_session):
        refresh(db_session, FakeProvider([ONE_PIECE + [result("[Erai] Frieren - 08 [1080p]")]]))
        titles = {w.display_title for w in db_session.execute(select(CatalogWork)).scalars()}
        assert titles == {"One Piece", "Frieren"}

    def test_a_movie_and_a_show_of_the_same_name_do_not_merge(self, db_session):
        refresh(
            db_session,
            FakeProvider([[
                result("Governor (2026) 1080p x264 -YTS", "YTS", 40, YTS_CATS),
                result("[Sub] Governor - 01 [1080p]", "Nyaa.si", 40, NYAA_CATS),
            ]]),
        )
        kinds = {w.kind for w in db_session.execute(select(CatalogWork)).scalars()}
        assert kinds == {"movie", "anime"}


class TestClassificationOnIngest:
    def test_nyaa_anime_does_not_land_in_movies(self, db_session):
        # The measured failure this whole module exists to prevent.
        refresh(db_session, FakeProvider([ONE_PIECE]))
        kinds = {r.kind for r in db_session.execute(select(CatalogRelease)).scalars()}
        assert kinds == {"anime"}

    def test_television_lands_in_series(self, db_session):
        refresh(db_session, FakeProvider([[
            result("Tuner.2025.720p.BluRay.x264-KNiVES", "The Pirate Bay", 2, TPB_TV_CATS)
        ]]))
        rel = db_session.execute(select(CatalogRelease)).scalar_one()
        assert rel.kind == "series"

    def test_non_video_never_enters_the_catalog(self, db_session):
        refresh(db_session, FakeProvider([[
            result("Some Album [FLAC]", "Nyaa.si", 40, [3000]),
            result("[Erai] Frieren - 08 [1080p]", "Nyaa.si", 40, NYAA_CATS),
        ]]))
        titles = [r.title for r in db_session.execute(select(CatalogRelease)).scalars()]
        assert len(titles) == 1 and "Frieren" in titles[0]

    def test_an_ungrabbable_release_is_not_ingested(self, db_session):
        bad = result("[Sub] Show - 01 [1080p]")
        bad.magnet = None
        bad.download_url = None
        refresh(db_session, FakeProvider([[bad]]))
        assert db_session.execute(select(CatalogRelease)).scalars().all() == []


class TestAccumulation:
    """Prowlarr exposes no pagination, so depth can only come from keeping what
    earlier passes saw."""

    def test_a_second_pass_keeps_what_the_first_one_found(self, db_session):
        later = [result("[Erai] Frieren - 09 [1080p]")]
        refresh(db_session, FakeProvider([ONE_PIECE, later]))
        refresh(db_session, FakeProvider([later]))

        titles = [r.title for r in db_session.execute(select(CatalogRelease)).scalars()]
        assert len(titles) == 4          # 3 One Piece kept + 1 new
        assert any("One Piece" in t for t in titles)

    def test_a_release_seen_again_is_updated_not_duplicated(self, db_session):
        # Same torrent, different link — which is exactly what the live Prowlarr
        # returns three seconds apart. Keying on the link would insert it twice.
        first = [result("[RLSP] One Piece 744-746 [BD 720p]", seeders=303)]
        again = [result("[RLSP] One Piece 744-746 [BD 720p]", seeders=12)]

        refresh(db_session, FakeProvider([first]))
        refresh(db_session, FakeProvider([again]))

        rows = db_session.execute(select(CatalogRelease)).scalars().all()
        assert len(rows) == 1
        assert rows[0].seeders == 12
        assert rows[0].missed_refreshes == 0

    def test_a_release_that_stops_appearing_ages_but_is_not_deleted(self, db_session):
        refresh(db_session, FakeProvider([ONE_PIECE]))
        for _ in range(3):
            refresh(db_session, FakeProvider([[result("[Erai] Frieren - 09 [1080p]")]]))

        one_piece = db_session.execute(
            select(CatalogRelease).where(CatalogRelease.title.like("%One Piece%"))
        ).scalars().all()
        assert len(one_piece) == 3
        assert all(r.missed_refreshes == 3 for r in one_piece)
        assert all(not r.stale for r in one_piece)

    def test_a_long_absent_release_goes_stale_and_stops_being_recommended(self, db_session):
        refresh(db_session, FakeProvider([ONE_PIECE]))
        rel = db_session.execute(select(CatalogRelease)).scalars().first()
        rel.missed_refreshes = CatalogRelease.STALE_AFTER
        db_session.commit()
        assert rel.stale


class TestRankingIsStoredPerIndexer:
    def test_an_indexer_reporting_no_seeders_is_not_buried(self, db_session):
        # YTS reports zero for two thirds of its catalogue. If those rows sorted
        # last on a raw count, the Movies rail would be empty of YTS entirely.
        refresh(db_session, FakeProvider([[
            result("Governor (2026) 1080p x264 -YTS", "YTS", 0, YTS_CATS),
            result("Big Baby (2025) 1080p x264 -YTS", "YTS", 0, YTS_CATS),
            result("[RLSP] One Piece 744-746 [BD 720p]", "Nyaa.si", 303, NYAA_CATS),
        ]]))
        by_title = {r.title: r for r in db_session.execute(select(CatalogRelease)).scalars()}
        yts = by_title["Governor (2026) 1080p x264 -YTS"]
        assert yts.seeder_pct == 0.5

    def test_a_work_carries_its_best_standing_for_the_rails(self, db_session):
        refresh(db_session, FakeProvider([ONE_PIECE]))
        work = db_session.execute(select(CatalogWork)).scalar_one()
        assert work.best_seeder_pct == 1.0


class TestThePassIsRecorded:
    """A refresh that dies does not break the wall, it just stops it getting
    deeper — which nobody notices for weeks unless it is written down."""

    def test_a_successful_pass_records_what_it_saw(self, db_session):
        refresh(db_session, FakeProvider([ONE_PIECE]))
        row = db_session.execute(select(CatalogRefresh)).scalar_one()
        assert row.finished_at is not None
        assert row.seen == 3 and row.added == 3
        assert row.per_indexer == {"Nyaa.si": 3}
        assert row.error is None

    def test_a_failed_pass_records_the_reason_rather_than_vanishing(self, db_session):
        out = refresh(db_session, FakeProvider([RuntimeError("Prowlarr unreachable")]))
        row = db_session.execute(select(CatalogRefresh)).scalar_one()
        assert "unreachable" in row.error
        assert row.finished_at is not None
        assert out["added"] == 0

    def test_a_failed_pass_leaves_the_existing_catalog_alone(self, db_session):
        refresh(db_session, FakeProvider([ONE_PIECE]))
        refresh(db_session, FakeProvider([RuntimeError("PC asleep")]))
        assert len(db_session.execute(select(CatalogRelease)).scalars().all()) == 3


class TestPredictedPlayback:
    def test_an_hevc_release_is_marked_as_needing_the_pc(self, db_session):
        refresh(db_session, FakeProvider([[
            result("[Sub] Show - 01 [1080p][x265][10bit]"),
            result("[Sub] Other - 01 [1080p][x264]"),
        ]]))
        by = {r.title: r.predicted_strategy for r in
              db_session.execute(select(CatalogRelease)).scalars()}
        assert by["[Sub] Show - 01 [1080p][x265][10bit]"] == "transcode_full"
        assert by["[Sub] Other - 01 [1080p][x264]"] == "remux"


class TestRecencyOrdering:
    """The wall leads with what is newest, and "newest" is the indexer's own
    publish date. first_seen_at is the same instant for everything ingested in
    one pass, so ordering on it falls back to insertion order and reads as
    shuffled — which is exactly how it looked."""

    def test_the_publish_date_is_stored_not_discarded(self, db_session):
        r = result("[Grp] Show - 01 [1080p]")
        r.published_at = "2026-08-01T10:00:00Z"
        refresh(db_session, FakeProvider([[r]]))
        rel = db_session.execute(select(CatalogRelease)).scalar_one()
        assert rel.published_at is not None
        assert rel.published_at.year == 2026 and rel.published_at.month == 8

    def test_a_work_takes_the_newest_date_among_its_releases(self, db_session):
        old = result("[Grp] Show - 01 [1080p]", seeders=10)
        new = result("[Grp] Show - 02 [1080p]", seeders=10)
        old.published_at = "2026-01-01T00:00:00Z"
        new.published_at = "2026-08-01T00:00:00Z"
        refresh(db_session, FakeProvider([[old, new]]))
        works = db_session.execute(select(CatalogWork)).scalars().all()
        assert len(works) == 1
        assert works[0].latest_release_at.month == 8

    def test_an_unreadable_date_does_not_lose_the_release(self, db_session):
        r = result("[Grp] Show - 01 [1080p]")
        r.published_at = "not-a-date"
        refresh(db_session, FakeProvider([[r]]))
        rel = db_session.execute(select(CatalogRelease)).scalar_one()
        assert rel.published_at is None
        # It still falls back to something sortable rather than dropping out of
        # the rail entirely.
        work = db_session.execute(select(CatalogWork)).scalar_one()
        assert work.latest_release_at is not None
