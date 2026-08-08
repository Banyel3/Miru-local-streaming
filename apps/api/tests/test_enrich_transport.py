"""Use case: the laptop's wifi drops while a backfill pass is running.

Enrichment asks three third-party APIs about every work. When one of them
cannot be reached, that is not an answer — but the code treated it as one, and
the answer it recorded was permanent. This file pins the distinction, because
it is invisible in normal operation and only shows up as forty cards that never
get art and never try again.
"""

import urllib.error

import pytest

from miru.catalog import enrich
from miru.catalog.models import CatalogWork


class TestATransportFailureIsNotAnAnswer:
    def test_a_provider_that_cannot_be_reached_raises_rather_than_returning_none(
        self, monkeypatch
    ):
        # None means "asked, and it has never heard of this". A socket that
        # never opened has not asked anything.
        def dead(*a, **kw):
            raise urllib.error.URLError("Network is unreachable")

        monkeypatch.setattr(enrich, "_get", dead)
        with pytest.raises(enrich.ProviderUnreachable):
            enrich._anilist("Frieren")

    def test_a_provider_that_answers_not_found_still_returns_none(self, monkeypatch):
        # The other half. Without this the fix would just be "never record a
        # miss", and the backfill would ask about the same unparseable release
        # every half hour forever.
        monkeypatch.setattr(enrich, "_get", lambda *a, **kw: {"data": {"Media": None}})
        assert enrich._anilist("Not A Real Show") is None

    def test_one_unreachable_provider_does_not_stop_the_next_from_answering(
        self, monkeypatch
    ):
        # AniList is asked first for anime and TMDB is the fallback. If AniList
        # is down and TMDB is up the work should still get its art — a
        # per-source failure is not a per-work failure.
        def dead(*a, **kw):
            raise enrich.ProviderUnreachable("anilist")

        monkeypatch.setattr(enrich, "_anilist", dead)
        monkeypatch.setattr(
            enrich, "_tmdb", lambda t, y, k: {"provider": "tmdb", "provider_id": "7"}
        )
        assert enrich.lookup("anime", "Frieren", None)["provider"] == "tmdb"

    def test_every_source_unreachable_is_reported_as_unreachable(self, monkeypatch):
        for name in ("_anilist", "_tvmaze", "_tmdb"):
            monkeypatch.setattr(
                enrich,
                name,
                lambda *a, **kw: (_ for _ in ()).throw(enrich.ProviderUnreachable("x")),
            )
        with pytest.raises(enrich.ProviderUnreachable):
            enrich.lookup("anime", "Frieren", None)


class TestTheWorkIsLeftAloneToBeAskedAgain:
    def test_an_unreachable_pass_does_not_mark_the_work_none(self, db_session, monkeypatch):
        """The reported failure, end to end.

        provider="none" is the mark that a work has been asked and the answer
        was no. Writing it when nobody was asked retires the work permanently:
        backfill only ever selects works whose provider is NULL, so a card that
        loses one pass to a dropped connection never gets art for the life of
        the database.

        The outage propagates rather than being reported as a miss, so a caller
        cannot mistake one for the other by ignoring a return value — which is
        exactly how the bug got in.
        """
        work = CatalogWork(kind="anime", normalised_title="frieren", display_title="Frieren")
        db_session.add(work)
        db_session.commit()

        def dead(*a, **kw):
            raise enrich.ProviderUnreachable("all sources")

        monkeypatch.setattr(enrich, "lookup", dead)
        with pytest.raises(enrich.ProviderUnreachable):
            enrich.enrich_work(db_session, work)
        db_session.rollback()
        assert work.provider is None, (
            "a work nobody could ask about was marked as asked, so the backfill "
            "will never select it again"
        )

    def test_a_genuine_miss_still_marks_the_work_so_it_is_not_asked_forever(
        self, db_session, monkeypatch
    ):
        work = CatalogWork(kind="anime", normalised_title="ncop-nced", display_title="NCOP+NCED")
        db_session.add(work)
        db_session.commit()
        monkeypatch.setattr(enrich, "lookup", lambda *a, **kw: None)
        assert enrich.enrich_work(db_session, work) is False
        db_session.commit()
        assert work.provider == "none"

    def test_an_unreachable_pass_does_not_poison_the_title_cache(self, db_session, monkeypatch):
        """The second, worse half of the same bug.

        resolve() caches both outcomes in title_resolutions, keyed on the
        normalised title. A miss cached during an outage outlives the outage
        AND applies to every other work with the same title, so re-running the
        backfill by hand does not fix it either.
        """
        from miru.catalog.models import TitleResolution
        from miru.catalog.resolve import resolve

        def dead(*a, **kw):
            raise enrich.ProviderUnreachable("all sources")

        monkeypatch.setattr(enrich, "lookup", dead)
        with pytest.raises(enrich.ProviderUnreachable):
            resolve(db_session, "anime", "Frieren", None)
        db_session.rollback()
        assert db_session.query(TitleResolution).count() == 0, (
            "an outage was written into the title cache, so the miss survives "
            "the outage and spreads to every work with this title"
        )


class TestTheBackfillSurvivesAnOutage:
    def test_an_outage_leaves_every_work_askable(self, db_session, monkeypatch):
        # The shape actually reported: the wifi drops for the ~40 seconds a
        # pass runs. Before the fix all 40 works came out marked "none".
        for i in range(5):
            db_session.add(CatalogWork(kind="anime", normalised_title=f"w{i}", display_title=f"Show {i}"))
        db_session.commit()

        def dead(*a, **kw):
            raise enrich.ProviderUnreachable("all sources")

        monkeypatch.setattr(enrich, "lookup", dead)
        enrich.backfill(db_session, limit=10)

        left = db_session.query(CatalogWork).filter(CatalogWork.provider.is_(None)).count()
        assert left == 5, f"{5 - left} works were retired by an outage"
