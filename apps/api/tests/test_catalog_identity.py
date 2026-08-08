"""Use case: one card per series, however the uploader spelled it.

Measured on the live catalogue: 111 anime works, 71 of them holding exactly one
release, and 21 title prefixes covering 47 works. The splits are romaji against
English against native script:

    "Youjo Senki 幼女戦記"  "Saga of Tanya the Evil"  "Youjo Senki 幼女戦記 Movie"

Those share no characters, so no amount of string cleaning merges them. AniList
says all three are 21613. Every test here is that fact turned into behaviour.
"""

import pytest
from sqlalchemy import select

from miru.catalog import enrich, resolve as resolve_mod
from miru.catalog.ingest import refresh
from miru.catalog.models import CatalogRelease, CatalogWork, TitleResolution

from tests.test_catalog_ingest import FakeProvider, result

TANYA = {
    "provider": "anilist",
    "provider_id": "21613",
    "display_title": "Saga of Tanya the Evil",
    "poster_url": "https://s4.anilist.co/tanya.jpg",
    "backdrop_url": None,
    "overview": "War.",
    "score": 7.8,
    "genres": ["Action"],
    "year": 2017,
    "format": "TV",
    "episode_count": 12,
}


@pytest.fixture(autouse=True)
def no_waiting(monkeypatch):
    """The rate limiter is real; its 0.8s per call is not what these test."""
    monkeypatch.setattr(enrich, "_MIN_INTERVAL", 0.0)


def fake_lookup(calls, answers):
    def lookup(kind, title, year):
        calls.append(title)
        return answers.get(title)

    return lookup


class TestIdentityComesFromTheProvider:
    def test_romaji_and_english_names_become_one_card(self, db_session, monkeypatch):
        # THE case. Before this, these were two cards that looked like two
        # different shows, and neither one ever grew.
        refresh(db_session, FakeProvider([[
            result("[Sub] Youjo Senki 幼女戦記 - 01 [1080p]"),
            result("[Sub] Saga of Tanya the Evil - 02 [1080p]"),
        ]]))
        assert len(db_session.execute(select(CatalogWork)).scalars().all()) == 2

        calls = []
        monkeypatch.setattr(enrich, "lookup", fake_lookup(calls, {
            "Youjo Senki 幼女戦記": TANYA,
            "Saga of Tanya the Evil": TANYA,
        }))
        enrich.backfill(db_session)

        works = db_session.execute(select(CatalogWork)).scalars().all()
        assert len(works) == 1
        assert works[0].display_title == "Saga of Tanya the Evil"
        assert (works[0].provider, works[0].provider_id) == ("anilist", "21613")
        # Both releases followed the merge rather than being orphaned.
        assert works[0].release_count == 2

    def test_a_later_release_under_a_third_name_joins_the_same_card(
        self, db_session, monkeypatch
    ):
        # The new-arrival path, which reads the cache and never calls out: a
        # refresh must not stall behind a rate-limited API.
        calls = []
        monkeypatch.setattr(enrich, "lookup", fake_lookup(calls, {
            "Youjo Senki 幼女戦記": TANYA,
        }))
        refresh(db_session, FakeProvider([[result("[Sub] Youjo Senki 幼女戦記 - 01 [1080p]")]]))
        enrich.backfill(db_session)
        calls.clear()

        db_session.add(TitleResolution(
            kind="anime", query="youjo senki 幼女戦記 movie",
            provider="anilist", provider_id="21613", data=TANYA,
        ))
        db_session.commit()
        refresh(db_session, FakeProvider([[
            result("[Sub] Youjo Senki 幼女戦記 Movie [1080p]"),
        ]]))

        works = db_session.execute(select(CatalogWork)).scalars().all()
        assert len(works) == 1 and works[0].release_count == 2
        assert calls == []

    def test_an_unresolvable_title_keeps_grouping_by_title(self, db_session, monkeypatch):
        # Nothing regresses for the releases no provider knows: they keep the
        # card they have today rather than collapsing into one another.
        monkeypatch.setattr(enrich, "lookup", fake_lookup([], {}))
        refresh(db_session, FakeProvider([[
            result("[Sub] Untraceable Thing - 01 [1080p]"),
            result("[Sub] Another Untraceable - 01 [1080p]"),
        ]]))
        enrich.backfill(db_session)

        works = db_session.execute(select(CatalogWork)).scalars().all()
        assert len(works) == 2
        assert {w.provider for w in works} == {"none"}

    def test_two_different_shows_are_not_merged_by_the_provider(self, db_session, monkeypatch):
        frieren = {**TANYA, "provider_id": "154587", "display_title": "Frieren", "year": 2023}
        monkeypatch.setattr(enrich, "lookup", fake_lookup([], {
            "Youjo Senki 幼女戦記": TANYA, "Frieren": frieren,
        }))
        refresh(db_session, FakeProvider([[
            result("[Sub] Youjo Senki 幼女戦記 - 01 [1080p]"),
            result("[Erai] Frieren - 08 [1080p]"),
        ]]))
        enrich.backfill(db_session)
        assert len(db_session.execute(select(CatalogWork)).scalars().all()) == 2


class TestResolutionIsCachedPerTitle:
    def test_a_title_is_asked_about_once_however_many_releases_it_has(
        self, db_session, monkeypatch
    ):
        # 193 anime releases in the live catalogue are well under a hundred
        # distinct names. Asking per release would be 193 requests against a
        # 90-a-minute limit for the same hundred answers.
        calls = []
        monkeypatch.setattr(enrich, "lookup", fake_lookup(calls, {"One Piece": TANYA}))
        refresh(db_session, FakeProvider([[
            result(f"[RLSP] One Piece {700 + i} [BD 720p]") for i in range(6)
        ]]))
        enrich.backfill(db_session)
        assert calls == ["One Piece"]

    def test_a_miss_is_remembered_so_it_is_not_asked_again(self, db_session, monkeypatch):
        calls = []
        monkeypatch.setattr(enrich, "lookup", fake_lookup(calls, {}))
        assert resolve_mod.resolve(db_session, "anime", "Nobody Knows", None) is None
        asked = list(calls)
        assert resolve_mod.resolve(db_session, "anime", "Nobody Knows", None) is None
        assert calls == asked

    def test_a_resolution_already_written_does_not_cost_the_work_its_art(
        self, db_session, monkeypatch
    ):
        # Live: five works lost their enrichment to a duplicate key on the cache
        # row. That row is a cache — losing the race for it is not a reason to
        # fail the work it was looked up for.
        monkeypatch.setattr(enrich, "lookup", fake_lookup([], {"Tampipi": TANYA}))
        resolve_mod.resolve(db_session, "anime", "Tampipi", None)
        db_session.commit()

        # The cache read missing a row that the write then collides with is the
        # shape of the failure, whatever produced the gap.
        monkeypatch.setattr(resolve_mod, "_row", lambda *a: None)
        assert resolve_mod.resolve(db_session, "anime", "Tampipi", None) == TANYA
        db_session.commit()


class TestATitleThatOnlyAlmostMatches:
    def test_a_release_tail_the_provider_never_heard_of_is_dropped(self, monkeypatch):
        # Measured: "Ore Monogatari NCOP NCED" and "One Piece S01E1172" miss on
        # the full string and hit on the first two words. 15 of the 33
        # unresolved anime titles come back this way.
        asked = []

        def anilist(term):
            asked.append(term)
            if term != "Ore Monogatari":
                return None
            return {**TANYA, "provider_id": "20946", "display_title": "My Love Story!!",
                    "names": ["Ore Monogatari!!", "My Love Story!!"]}

        monkeypatch.setattr(enrich, "_anilist", anilist)
        got = enrich.lookup("anime", "Ore Monogatari NCOP NCED", None)

        assert got["provider_id"] == "20946"
        assert asked == ["Ore Monogatari NCOP NCED", "Ore Monogatari NCOP", "Ore Monogatari"]

    def test_the_match_is_checked_against_every_name_not_the_shown_one(self, monkeypatch):
        # AniList shows English and the release names romaji, so checking the
        # displayed title alone rejects the merge this exists to make: "Saga of
        # Tanya the Evil" is nowhere in "Youjo Senki 幼女戦記 Movie".
        monkeypatch.setattr(enrich, "_anilist", lambda t: (
            {**TANYA, "names": ["Youjo Senki", "Saga of Tanya the Evil", "幼女戦記"]}
            if t == "Youjo Senki" else None
        ))
        got = enrich.lookup("anime", "Youjo Senki 幼女戦記 Movie", None)
        assert got["provider_id"] == "21613"

    def test_a_shortened_search_that_finds_something_else_is_refused(self, monkeypatch):
        # "Detective Conan Movie 2 The Fourteenth Target" shortens to a hit on a
        # *different* Conan film. Taking it would put this release on that
        # film's card, which offers the wrong download — worse than the split it
        # would have fixed.
        monkeypatch.setattr(enrich, "_anilist", lambda t: (
            {**TANYA, "provider_id": "169754", "format": "MOVIE",
             "display_title": "Detective Conan: The Million-dollar Pentagram",
             "names": ["Meitantei Conan: 100-man Dollar no Michishirube",
                       "Detective Conan: The Million-dollar Pentagram"]}
            if t == "Detective Conan Movie 2" else None
        ))
        monkeypatch.setattr(enrich, "_tmdb", lambda *a: None)
        assert enrich.lookup(
            "anime", "Detective Conan Movie 2 The Fourteenth Target", None
        ) is None

    def test_the_good_source_is_exhausted_before_the_fallback_is_asked(self, monkeypatch):
        # AniList misses "Youjo Senki 幼女戦記" on the full string and TMDB
        # matches it. Asking TMDB first puts the same show on two cards under
        # two providers, which is the split this module exists to close.
        monkeypatch.setattr(enrich, "_anilist", lambda t: (
            {**TANYA, "names": ["Youjo Senki"]} if t == "Youjo Senki" else None
        ))
        monkeypatch.setattr(enrich, "_tmdb", lambda *a: {"provider": "tmdb"})
        assert enrich.lookup("anime", "Youjo Senki 幼女戦記", None)["provider"] == "anilist"


class TestTheProvidersAreNotHammered:
    def test_every_outbound_request_waits_its_turn(self, monkeypatch):
        # AniList allows 90 a minute and answers 429 for the rest of the minute
        # once you pass it, so the pass that trips it enriches nothing.
        import time

        monkeypatch.setattr(enrich, "_MIN_INTERVAL", 0.05)
        monkeypatch.setattr(enrich, "_last_call", 0.0)
        monkeypatch.setattr(enrich.urllib.request, "urlopen", _boom)

        started = time.monotonic()
        for _ in range(3):
            enrich._tvmaze("Anything")
        assert time.monotonic() - started >= 0.1


def _boom(*a, **kw):
    raise OSError("no network in tests")


class TestRenamingACardMovesItsGroupingKey:
    def test_the_display_name_and_the_grouping_key_stay_the_same_thing(
        self, db_session, monkeypatch
    ):
        # The live bug: enrichment set display_title to the provider's name and
        # left normalised_title as the parsed one, so a card read "Saga of Tanya
        # the Evil" while still grouping as "youjo senki" — and the next release
        # under the provider's name made a second, identical-looking card.
        monkeypatch.setattr(enrich, "lookup", fake_lookup([], {
            "Youjo Senki 幼女戦記": TANYA,
        }))
        refresh(db_session, FakeProvider([[result("[Sub] Youjo Senki 幼女戦記 - 01 [1080p]")]]))
        enrich.backfill(db_session)

        work = db_session.execute(select(CatalogWork)).scalar_one()
        assert work.display_title == "Saga of Tanya the Evil"
        assert work.normalised_title == "saga of tanya the evil"
        assert work.year == 2017


class TestOnePassResolvesManyCards:
    def test_a_merge_does_not_take_the_rest_of_the_pass_down_with_it(
        self, db_session, monkeypatch
    ):
        # Hit on the live catalogue: the pass held instances of works that an
        # earlier merge had deleted, and flushing one raised StaleDataError —
        # 400 works in, and every work after it went unresolved.
        monkeypatch.setattr(enrich, "lookup", fake_lookup([], {
            "Youjo Senki 幼女戦記": TANYA,
            "Saga of Tanya the Evil": TANYA,
            "Frieren": {**TANYA, "provider_id": "154587", "display_title": "Frieren"},
        }))
        refresh(db_session, FakeProvider([[
            result("[Sub] Youjo Senki 幼女戦記 - 01 [1080p]"),
            result("[Sub] Saga of Tanya the Evil - 02 [1080p]"),
            result("[Erai] Frieren - 08 [1080p]"),
        ]]))
        out = enrich.backfill(db_session)

        assert out["found"] == 3
        assert len(db_session.execute(select(CatalogWork)).scalars().all()) == 2


class TestGhostWorks:
    def _ghost(self, db, **kw) -> CatalogWork:
        """A card whose releases have gone elsewhere — 21 of these were live."""
        w = CatalogWork(
            kind="anime", normalised_title="ghost", display_title="Ghost", genres=[], **kw
        )
        db.add(w)
        db.commit()
        return w

    def test_a_work_with_no_releases_left_is_deleted(self, db_session, monkeypatch):
        # Invisible on the wall — the rails filter on release_count — but still
        # holding the unique title the card that absorbed its releases needs.
        monkeypatch.setattr(enrich, "lookup", fake_lookup([], {}))
        ghost = self._ghost(db_session)

        refresh(db_session, FakeProvider([[result("[Sub] Frieren - 01 [1080p]")]]))
        assert db_session.get(CatalogWork, ghost.id) is None

    def test_a_work_holding_a_download_survives_having_no_releases(
        self, db_session, monkeypatch
    ):
        # Deleting this one loses the only record of a download in flight, and
        # the card that replaces it offers to grab the same thing again.
        monkeypatch.setattr(enrich, "lookup", fake_lookup([], {}))
        ghost = self._ghost(db_session, download_job_id="job-1")

        refresh(db_session, FakeProvider([[result("[Sub] Frieren - 01 [1080p]")]]))
        assert db_session.get(CatalogWork, ghost.id) is not None


class TestFilmsAreToldFromShowsWithoutAFifthPill:
    def test_the_providers_format_reaches_the_api(self, client, db_session, monkeypatch):
        film = {**TANYA, "provider_id": "21", "display_title": "One Piece Film: Red",
                "format": "MOVIE", "episode_count": None}
        monkeypatch.setattr(enrich, "lookup", fake_lookup([], {
            "Youjo Senki 幼女戦記": TANYA, "One Piece Film Red": film,
        }))
        refresh(db_session, FakeProvider([[
            result("[Sub] Youjo Senki 幼女戦記 - 01 [1080p]"),
            result("[Sub] One Piece Film Red [1080p]"),
        ]]))
        enrich.backfill(db_session)

        by_title = {
            w.display_title: w for w in db_session.execute(select(CatalogWork)).scalars()
        }
        detail = client.get(f"/api/catalog/works/{by_title['One Piece Film: Red'].id}").json()
        assert detail["format"] == "MOVIE"
        assert detail["episode_count"] is None

        series = client.get(f"/api/catalog/works/{by_title['Saga of Tanya the Evil'].id}").json()
        assert series["format"] == "TV"
        # What a season sheet needs to say "1 of 12" rather than listing 12 dead
        # rows: the count from the provider, the episodes from the releases.
        assert series["episode_count"] == 12
        assert [r["episode"] for r in series["releases"]] == [1]
