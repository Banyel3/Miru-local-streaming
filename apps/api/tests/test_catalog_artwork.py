"""Use case: cards that show what the thing looks like.

Torrent indexers carry no artwork, so a wall without this is a list with extra
whitespace. Three sources, and the split matters: anime and series need no
configuration, film needs a key and is the only kind that does.
"""

import pytest

from miru.catalog import enrich
from miru.catalog.classify import classify
from miru.catalog.models import CatalogWork


class TestAdultContentIsExcludedFirst:
    def test_a_release_tagged_both_xxx_and_movies_is_not_a_movie(self):
        # Same dual-tagging problem measured on Nyaa, different consequence: a
        # search for "filipino" returned 24 adult results among 251, and letting
        # the Movies tag win puts them on the wall between two films.
        assert classify([6000, 2000]) is None
        assert classify([6040, 2040]) is None

    def test_ordinary_films_are_unaffected(self):
        assert classify([2040, 100045]) == "movie"


class TestChoosingASource:
    """Ordered by which source actually knows the thing, not by preference."""

    def test_anime_goes_to_anilist(self, monkeypatch):
        monkeypatch.setattr(enrich, "_anilist", lambda t: {"provider": "anilist"})
        monkeypatch.setattr(enrich, "_tmdb", lambda *a: {"provider": "tmdb"})
        assert enrich.lookup("anime", "One Piece", None)["provider"] == "anilist"

    def test_series_goes_to_tvmaze_once_anilist_has_said_no(self, monkeypatch):
        # AniList is asked first for every kind now — it holds anime and nothing
        # else, so an answer from it is a claim about the show rather than about
        # the indexer that carried the release. TVmaze is where a live-action
        # series lands, which is after that question has been asked and missed.
        monkeypatch.setattr(enrich, "_anilist", lambda t: None)
        monkeypatch.setattr(enrich, "_tvmaze", lambda t: {"provider": "tvmaze"})
        monkeypatch.setattr(enrich, "_tmdb", lambda *a: {"provider": "tmdb"})
        assert enrich.lookup("series", "Big Brother", None)["provider"] == "tvmaze"

    def test_a_keyless_source_falls_through_to_tmdb(self, monkeypatch):
        monkeypatch.setattr(enrich, "_anilist", lambda t: None)
        monkeypatch.setattr(enrich, "_tmdb", lambda *a: {"provider": "tmdb"})
        assert enrich.lookup("anime", "Obscure", None)["provider"] == "tmdb"

    def test_film_without_a_key_finds_nothing_rather_than_erroring(self, monkeypatch):
        from miru.core.config import settings

        monkeypatch.setattr(settings, "tmdb_api_key", "")
        assert enrich.lookup("movie", "Hello Love Goodbye", 2019) is None


class TestBackfill:
    def _work(self, db, **kw):
        w = CatalogWork(
            kind=kw.pop("kind", "anime"),
            normalised_title=kw.pop("norm", "show"),
            display_title=kw.pop("title", "Show"),
            genres=[],
            **kw,
        )
        db.add(w)
        db.commit()
        return w

    def test_art_is_written_onto_the_work(self, db_session, monkeypatch):
        w = self._work(db_session)
        monkeypatch.setattr(
            enrich,
            "lookup",
            lambda *a: {
                "provider": "anilist",
                "provider_id": "21",
                "display_title": "ONE PIECE",
                "poster_url": "https://s4.anilist.co/x.jpg",
                "backdrop_url": None,
                "overview": "Pirates.",
                "score": 8.7,
                "genres": ["Action"],
                "year": 1999,
            },
        )
        assert enrich.backfill(db_session)["found"] == 1
        db_session.refresh(w)
        assert w.poster_url == "https://s4.anilist.co/x.jpg"
        # The indexer's parsed title is a filename; the provider's is the name
        # of the thing.
        assert w.display_title == "ONE PIECE"
        assert w.score == 8.7

    def test_a_work_with_no_match_is_not_asked_about_forever(self, db_session, monkeypatch):
        # Otherwise every refresh re-queries the same untraceable release.
        w = self._work(db_session)
        monkeypatch.setattr(enrich, "lookup", lambda *a: None)
        enrich.backfill(db_session)
        db_session.refresh(w)
        assert w.provider == "none"
        assert enrich.backfill(db_session)["attempted"] == 0

    def test_learning_a_year_merges_two_cards_for_one_film(self, db_session, monkeypatch):
        # Real case, hit on the live catalogue: one release named the year and
        # one did not, so "Alapaap" became two works. Enrichment is the moment
        # they are revealed to be one thing, so it merges rather than failing on
        # the unique constraint.
        from miru.catalog.models import CatalogRelease

        dated = self._work(db_session, kind="movie", norm="alapaap", title="Alapaap", year=2022)
        undated = self._work(db_session, kind="movie", norm="alapaap", title="Alapaap")
        db_session.add(
            CatalogRelease(
                info_hash="a" * 40, indexer="X", guid="g", title="Alapaap 1080p",
                kind="movie", work_id=undated.id, categories=[],
            )
        )
        db_session.commit()

        monkeypatch.setattr(
            enrich, "lookup",
            lambda *a: {"provider": "tmdb", "provider_id": "1", "display_title": "Alapaap",
                        "poster_url": "https://image.tmdb.org/t/p/w500/x.jpg",
                        "backdrop_url": None, "overview": None, "score": 3.7,
                        "genres": [], "year": 2022},
        )
        enrich.backfill(db_session)

        remaining = db_session.query(CatalogWork).filter_by(normalised_title="alapaap").all()
        assert len(remaining) == 1
        assert remaining[0].id == dated.id
        # The release follows the card it was folded into, rather than being
        # orphaned or deleted with it.
        rel = db_session.query(CatalogRelease).one()
        assert rel.work_id == dated.id

    def test_one_bad_title_does_not_stop_the_rest(self, db_session, monkeypatch):
        self._work(db_session, norm="a", title="A")
        self._work(db_session, norm="b", title="B")

        def flaky(kind, title, year):
            if title == "A":
                raise RuntimeError("provider exploded")
            return {"provider": "anilist", "provider_id": "1", "display_title": "B",
                    "poster_url": "https://s4.anilist.co/b.jpg", "backdrop_url": None,
                    "overview": None, "score": None, "genres": [], "year": None}

        monkeypatch.setattr(enrich, "lookup", flaky)
        assert enrich.backfill(db_session)["found"] == 1


class TestThePosterProxy:
    def test_only_allowlisted_hosts_are_ever_fetched(self):
        from miru.catalog.posters import _allowed

        # These URLs come out of a third-party API response. Without the
        # allowlist that service chooses what our server requests.
        assert _allowed("https://image.tmdb.org/t/p/w500/x.jpg")
        assert _allowed("https://s4.anilist.co/file/x.jpg")
        assert not _allowed("https://evil.example.com/x.jpg")
        assert not _allowed("http://image.tmdb.org/x.jpg")   # plaintext
        assert not _allowed("file:///etc/passwd")

    def test_a_work_without_art_is_a_404_so_the_card_can_fall_back(self, client, db_session):
        w = CatalogWork(kind="movie", normalised_title="x", display_title="X", genres=[])
        db_session.add(w)
        db_session.commit()
        # Not a placeholder image and not an empty 200: the client needs to be
        # able to tell, so it can render ArtTile instead of a broken frame.
        assert client.get(f"/api/posters/{w.id}").status_code == 404

    def test_an_unknown_work_is_a_404(self, client):
        assert client.get("/api/posters/999999").status_code == 404


class TestTheWallExplainsMissingFilmArt:
    def test_it_reports_how_many_films_have_no_cover(self, client, db_session):
        for i in range(3):
            db_session.add(
                CatalogWork(kind="movie", normalised_title=f"f{i}", display_title=f"F{i}", genres=[])
            )
        db_session.commit()
        b = client.get("/api/catalog").json()["artwork"]
        assert b["tmdb_configured"] is False
        assert b["films_without_art"] == 3

    def test_it_stops_complaining_once_a_key_is_set(self, client, db_session, monkeypatch):
        from miru.core.config import settings

        monkeypatch.setattr(settings, "tmdb_api_key", "abc")
        assert client.get("/api/catalog").json()["artwork"]["tmdb_configured"] is True


class TestUndecodableFiles:
    """A DRM-encrypted release is not a transcoding problem and must not be
    worded like one. Measured on a real Crunchyroll WEB-DL whose video track was
    never decrypted."""

    def _encrypted(self):
        from miru.transcode.strategy import Probe

        # What ffprobe actually returns for it: no codec, but real dimensions.
        return Probe(container="matroska", video_codec=None, video_tag="encv",
                     audio_codec="aac", width=1920, height=1080)

    def test_an_encrypted_track_is_unplayable_not_direct(self):
        from miru.transcode.strategy import resolve_strategy

        # Previously this returned `direct` on the optimistic branch, and the
        # user got a black player forever with no explanation.
        assert resolve_strategy(self._encrypted()) == "unplayable"

    def test_a_genuinely_unprobed_file_is_still_optimistic(self):
        from miru.transcode.strategy import Probe, resolve_strategy

        # The distinguishing signal is the dimensions: an encrypted track has
        # them, an unprobed file does not.
        assert resolve_strategy(Probe()) == "direct"

    def test_it_never_reaches_the_gpu(self):
        from miru.transcode.worker import NEEDS_WORKER

        assert "unplayable" not in NEEDS_WORKER

    def test_the_note_says_what_is_wrong_and_what_to_do(self, monkeypatch):
        from miru.transcode.worker import availability

        state, note = availability("unplayable")
        assert state == "unplayable"
        assert "encrypted" in note.lower()
        # Not "the PC is offline" — waking it would not help.
        assert "offline" not in note.lower()


class TestRemuxStaysOnTheLaptop:
    def test_remux_never_requires_the_pc(self):
        # DEPLOYMENT.md lists this among the decisions not to re-litigate:
        # remux is stream-copy, measured at 0.37s CPU per ten-minute film, and
        # sending it to the PC would make the PC a hard dependency for the
        # majority rung of an anime library.
        from miru.transcode.worker import NEEDS_WORKER, availability

        assert "remux" not in NEEDS_WORKER
        assert availability("remux")[0] == "available"


class TestAniListSaysTheShapeOfTheRun:
    """The wall cannot tell a complete show from a fragment without a
    denominator. AniList gives both halves: `episodes` for a finished run, and
    `nextAiringEpisode` for how much of an airing one exists yet — the field
    Jikan/MAL cannot provide (checked live: One Piece answers episodes=None
    there). No new provider; the query we already run says it.
    """

    def _media(self, **kw):
        base = {
            "id": 1, "title": {"romaji": "Show"}, "format": "TV",
            "startDate": {"year": 2023},
        }
        return {"data": {"Media": {**base, **kw}}}

    def test_a_finished_show_reports_its_status(self, monkeypatch):
        monkeypatch.setattr(
            enrich, "_get",
            lambda *a, **k: self._media(status="FINISHED", episodes=28),
        )
        got = enrich._anilist("Show")
        assert got["release_status"] == "FINISHED"
        assert got["episode_count"] == 28
        assert got["episodes_aired"] is None

    def test_an_airing_show_reports_how_much_exists(self, monkeypatch):
        # nextAiringEpisode is the NEXT one, so aired-so-far is one less.
        monkeypatch.setattr(
            enrich, "_get",
            lambda *a, **k: self._media(
                status="RELEASING", episodes=None,
                nextAiringEpisode={"episode": 7},
            ),
        )
        got = enrich._anilist("Show")
        assert got["release_status"] == "RELEASING"
        assert got["episodes_aired"] == 6

    def test_an_airing_show_between_schedules_reports_nothing_not_zero(self, monkeypatch):
        # A show on hiatus has no nextAiringEpisode. Zero would mean "nothing
        # has aired", which would hide a show someone holds completely.
        monkeypatch.setattr(
            enrich, "_get",
            lambda *a, **k: self._media(status="RELEASING", episodes=None),
        )
        assert enrich._anilist("Show")["episodes_aired"] is None


class TestADeadDiskDegradesInsteadOf500ing:
    """The storage USB drive dropped and re-enumerated; the stale mount
    answered EIO to everything. Every poster on the site turned into a 500 —
    "images are all broken" — when a 404 would have left the tint tiles up and
    the site usable. A cache being unavailable is a miss, not a server error.
    """

    def test_a_cache_dir_that_cannot_be_made_is_a_miss_not_a_crash(
        self, client, db_session, monkeypatch
    ):
        from miru.catalog import posters as mod
        from miru.catalog.models import CatalogWork

        w = CatalogWork(kind="anime", normalised_title="p", display_title="P",
                        poster_url="https://image.tmdb.org/t/p/w500/x.jpg")
        db_session.add(w)
        db_session.commit()

        def dead():
            raise OSError(5, "Input/output error")

        monkeypatch.setattr(mod, "_cache_dir", dead)
        assert client.get(f"/api/posters/{w.id}").status_code == 404

    def test_health_reports_whether_storage_answers(self, client, monkeypatch):
        import miru.main as main_mod

        monkeypatch.setattr(main_mod, "_storage_ok", lambda: False)
        assert client.get("/api/health").json()["storage_ok"] is False


class TestLiveActionGetsARealDenominator:
    """The strict wall exempted live-action series only because no wired
    provider gave their episode counts. TVmaze's aired-episode list and TMDB's
    tv detail both do — one extra request each, behind the same throttle — so
    the exemption ends and Kamen-Rider-style fragments leave the wall too.
    """

    def test_tvmaze_counts_the_aired_list(self, monkeypatch):
        calls = []

        def fake_get(url, *a, **k):
            calls.append(url)
            if "/search/shows" in url:
                return [{"show": {"id": 7, "name": "Show", "status": "Ended",
                                  "premiered": "2020-01-01"}}]
            assert url.endswith("/shows/7/episodes")
            return [{"id": i} for i in range(26)]

        monkeypatch.setattr(enrich, "_get", fake_get)
        got = enrich._tvmaze("Show")
        assert got["episode_count"] == 26
        assert got["episodes_aired"] == 26
        assert got["release_status"] == "FINISHED"

    def test_a_running_show_maps_to_releasing(self, monkeypatch):
        def fake_get(url, *a, **k):
            if "/search/shows" in url:
                return [{"show": {"id": 7, "name": "Show", "status": "Running"}}]
            return [{"id": i} for i in range(6)]

        monkeypatch.setattr(enrich, "_get", fake_get)
        got = enrich._tvmaze("Show")
        assert got["release_status"] == "RELEASING"
        assert got["episodes_aired"] == 6

    def test_a_failed_episode_list_does_not_lose_the_show(self, monkeypatch):
        # The identity is worth more than the count; a second request failing
        # must not turn a resolved show into a miss.
        def fake_get(url, *a, **k):
            if "/search/shows" in url:
                return [{"show": {"id": 7, "name": "Show", "status": "Ended"}}]
            raise OSError("episodes endpoint down")

        monkeypatch.setattr(enrich, "_get", fake_get)
        got = enrich._tvmaze("Show")
        assert got["provider_id"] == "7"
        assert got["episodes_aired"] is None

    def test_tmdb_series_detail_supplies_the_count(self, monkeypatch):
        monkeypatch.setattr(enrich.settings, "tmdb_api_key", "k")

        def fake_get(url, *a, **k):
            if "/search/tv" in url:
                return {"results": [{"id": 9, "name": "Show", "first_air_date": "2020-01-01"}]}
            assert "/tv/9" in url
            return {"number_of_episodes": 40, "status": "Ended"}

        monkeypatch.setattr(enrich, "_get", fake_get)
        got = enrich._tmdb("Show", None, "series")
        assert got["episode_count"] == 40
        assert got["release_status"] == "FINISHED"

    def test_tmdb_movies_never_pay_for_a_detail_call(self, monkeypatch):
        monkeypatch.setattr(enrich.settings, "tmdb_api_key", "k")
        calls = []

        def fake_get(url, *a, **k):
            calls.append(url)
            return {"results": [{"id": 9, "title": "Film", "release_date": "2020-01-01"}]}

        monkeypatch.setattr(enrich, "_get", fake_get)
        enrich._tmdb("Film", None, "movie")
        assert len(calls) == 1
