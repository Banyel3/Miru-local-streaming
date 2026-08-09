"""Use case: opening a series card and finding the whole series.

Ingest only ever sees each indexer's front page, which is about a day deep with
`limit` ignored and `offset` returning nothing. So a card holds the encodings of
the few episodes uploaded this week: measured, ONE PIECE had 206 releases
covering 82 distinct episodes of 1172, and BLACK TORCH had 26 releases covering
5 episodes.

No filter can fix that — the episodes were never fetched. Asking can: measured
live, `one piece batch` returns 126 results and all of them are packs. So opening
a card asks.

Nothing here talks to Prowlarr; the provider is faked at the seam.
"""

from datetime import datetime, timedelta, timezone

import pytest

from miru.catalog import sweep as sweep_mod
from miru.catalog.models import CatalogWork


@pytest.fixture
def searches(monkeypatch):
    """Record what was asked for, and answer with nothing."""
    asked: list[str] = []

    class Fake:
        def search(self, q, limit=50):
            asked.append(q)
            return []

    monkeypatch.setattr(sweep_mod, "provider", Fake())
    monkeypatch.setattr(sweep_mod, "ingest_search", lambda *a, **k: None)
    return asked


def _work(db, title="ONE PIECE", kind="anime", swept=None):
    w = CatalogWork(
        kind=kind, normalised_title=title.casefold(), display_title=title, swept_at=swept
    )
    db.add(w)
    db.commit()
    return w


class TestOpeningACardLooksForPacks:
    def test_it_asks_for_the_batch(self, db_session, searches):
        sweep_mod.sweep(db_session, _work(db_session))
        assert any("batch" in q.lower() for q in searches), searches

    def test_it_also_asks_the_other_word(self, db_session, searches):
        # `spy x family complete` is the only query that surfaces the Trix
        # season pack at all — "batch" alone does not find it.
        sweep_mod.sweep(db_session, _work(db_session, "Spy x Family"))
        assert any("complete" in q.lower() for q in searches), searches

    def test_it_asks_about_the_show_it_was_given(self, db_session, searches):
        sweep_mod.sweep(db_session, _work(db_session, "Frieren"))
        assert all("frieren" in q.lower() for q in searches), searches


class TestItDoesNotAskAgainAndAgain:
    def test_a_second_open_within_the_window_asks_nothing(self, db_session, searches):
        """A card polls while it is open.

        Sweeping per request is the shape that just cost a day: the live remux
        started one ffmpeg per poll. A search per poll would hammer four
        indexers instead.
        """
        w = _work(db_session)
        sweep_mod.sweep(db_session, w)
        db_session.commit()
        before = len(searches)
        sweep_mod.sweep(db_session, w)
        assert len(searches) == before

    def test_a_work_swept_yesterday_is_swept_again(self, db_session, searches):
        # The front page turns over in about a day, which is the same window.
        old = datetime.now(timezone.utc) - timedelta(days=2)
        sweep_mod.sweep(db_session, _work(db_session, swept=old))
        assert searches

    def test_the_timestamp_is_written_even_when_nothing_came_back(
        self, db_session, searches
    ):
        # Otherwise a show with no packs is searched on every single open.
        w = _work(db_session)
        sweep_mod.sweep(db_session, w)
        db_session.commit()
        assert w.swept_at is not None


class TestItNeverBreaksTheCard:
    def test_an_indexer_error_leaves_the_card_working(self, db_session, monkeypatch):
        from miru.acquisition.provider import AcquisitionError

        class Broken:
            def search(self, q, limit=50):
                raise AcquisitionError("Prowlarr is unreachable")

        monkeypatch.setattr(sweep_mod, "provider", Broken())
        sweep_mod.sweep(db_session, _work(db_session))  # must not raise

    def test_a_film_is_not_swept(self, db_session, searches):
        # A film has no episodes to be missing, and "batch" would return
        # somebody's whole filmography.
        sweep_mod.sweep(db_session, _work(db_session, "Monay", kind="movie"))
        assert searches == []

    def test_a_work_with_no_title_is_not_swept(self, db_session, searches):
        sweep_mod.sweep(db_session, _work(db_session, " "))
        assert searches == []


class TestTheCardTriggersItWithoutWaitingOnIt:
    def test_opening_a_card_starts_a_sweep(self, client, db_session, monkeypatch):
        from miru.catalog import router as mod

        fired = []
        monkeypatch.setattr(mod, "_request_sweep", lambda wid: fired.append(wid))
        w = _work(db_session)
        client.get(f"/api/catalog/works/{w.id}")
        assert fired == [w.id]

    def test_the_card_does_not_wait_for_the_indexers(self, client, db_session, monkeypatch):
        """The response must not block on four indexers.

        A pack query takes seconds. Opening a card behind it would make every
        card feel broken, and the sheet already has a loading state for the
        releases it can show immediately.
        """
        import time

        from miru.catalog import router as mod

        def slow(_wid):
            time.sleep(3)

        monkeypatch.setattr(mod, "sweep_now", slow)
        w = _work(db_session)
        started = time.monotonic()
        res = client.get(f"/api/catalog/works/{w.id}")
        assert res.status_code == 200
        assert time.monotonic() - started < 2.0, "the card waited for the sweep"

    def test_a_missing_work_does_not_start_one(self, client, db_session, monkeypatch):
        from miru.catalog import router as mod

        fired = []
        monkeypatch.setattr(mod, "_request_sweep", lambda wid: fired.append(wid))
        assert client.get("/api/catalog/works/999999").status_code == 404
        assert fired == []


class TestTheSweepAsksEveryNameTheShowGoesBy:
    """Measured live: 'Frieren: Beyond Journey's End batch' finds 13 results;
    'Sousou no Frieren batch' finds 49. Packs are named by fansub groups in
    romaji, and the sweep only ever asked the provider's canonical title — so
    most cards saw 0 results and stayed incomplete, which is the reported bug.

    The naming variants are already in the catalogue: the releases' own
    parsed titles are the strings that actually appear on the indexers.
    """

    def _with_variants(self, db):
        from miru.catalog.models import CatalogRelease

        w = _work(db, "Frieren: Beyond Journey's End")
        for i, pt in enumerate(
            ["Sousou no Frieren", "Sousou no Frieren", "Sousou no Frieren",
             "Frieren Beyond Journeys End"]
        ):
            db.add(CatalogRelease(
                info_hash=f"{i:040x}", indexer="Nyaa.si", guid=f"g{i}",
                title=f"{pt} - 0{i}", kind="anime", work_id=w.id, parsed_title=pt,
                seeder_pct=0.5, seeders=5, leechers=0, size_bytes=1,
                magnet=f"magnet:?xt=urn:btih:{i:040x}",
            ))
        db.commit()
        return w

    def test_the_romaji_variant_is_asked_too(self, db_session, searches):
        w = self._with_variants(db_session)
        sweep_mod.sweep(db_session, w)
        assert any("sousou no frieren" in q.lower() for q in searches), searches

    def test_the_canonical_title_is_still_asked(self, db_session, searches):
        w = self._with_variants(db_session)
        sweep_mod.sweep(db_session, w)
        assert any("frieren: beyond" in q.lower() for q in searches), searches

    def test_near_duplicate_variants_are_not_asked_twice(self, db_session, searches):
        # "Frieren Beyond Journeys End" is the display title minus punctuation;
        # asking both is the same question twice at four indexers.
        w = self._with_variants(db_session)
        sweep_mod.sweep(db_session, w)
        asked = [q.lower() for q in searches]
        assert not any("frieren beyond journeys end" in q for q in asked), searches

    def test_the_request_count_is_bounded(self, db_session, searches):
        # A show with twenty naming variants must not fire forty searches on a
        # card open. Cap: 3 names × 2 terms.
        from miru.catalog.models import CatalogRelease

        w = _work(db_session, "Show")
        for i in range(20):
            db_session.add(CatalogRelease(
                info_hash=f"{i+100:040x}", indexer="Nyaa.si", guid=f"h{i}",
                title=f"Variant {i} - 01", kind="anime", work_id=w.id,
                parsed_title=f"Variant {i}", seeder_pct=0.5, seeders=5,
                leechers=0, size_bytes=1, magnet=f"magnet:?xt=urn:btih:{i+100:040x}",
            ))
        db_session.commit()
        sweep_mod.sweep(db_session, w)
        assert len(searches) <= 6, f"{len(searches)} searches for one card open"


class TestTheSheetKnowsASweepIsComing:
    def test_the_payload_says_when_a_sweep_was_kicked(self, client, db_session, monkeypatch):
        # The sweep is async; without this flag the open sheet shows the packs
        # only after a close-and-reopen, which reads as "it isn't fetching".
        from miru.catalog import router as mod

        monkeypatch.setattr(mod, "_request_sweep", lambda wid: None)
        w = _work(db_session, "Never Swept")
        assert client.get(f"/api/catalog/works/{w.id}").json()["sweep_started"] is True

    def test_a_recently_swept_work_promises_nothing(self, client, db_session, monkeypatch):
        from datetime import datetime, timezone

        from miru.catalog import router as mod

        monkeypatch.setattr(mod, "_request_sweep", lambda wid: None)
        w = _work(db_session, "Fresh")
        w.swept_at = datetime.now(timezone.utc)
        db_session.commit()
        assert client.get(f"/api/catalog/works/{w.id}").json()["sweep_started"] is False
