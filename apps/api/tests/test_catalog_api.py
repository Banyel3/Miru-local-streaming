"""Use case: the browse wall, through the API the screen actually calls."""

import pytest
from tests.test_catalog_ingest import NYAA_CATS, TPB_TV_CATS, YTS_CATS, FakeProvider, result

from miru.catalog.ingest import refresh


class FakeDownloader:
    """Stands in for whichever backend is configured.

    `sequential` is captured rather than ignored: it is the whole difference
    between Watch Now meaning "in a moment" and meaning "when it finishes".
    """

    def __init__(self, status=None, fail_status=None):
        self.submitted = []
        self._status = status
        self._fail = fail_status

    def submit(self, magnet, *, sequential=False):
        self.submitted.append((magnet, sequential))
        return type("J", (), {"id": "gid1", "result_id": magnet})()

    def status(self, job_id):
        if self._fail:
            raise self._fail
        return self._status


@pytest.fixture
def filled(db_session):
    """A catalog shaped like a real one: plenty of anime, thin on TV."""
    anime = [
        result(f"[Grp] Show {i} - 01 [1080p][x264]", "Nyaa.si", 300 - i * 10, NYAA_CATS)
        for i in range(12)
    ]
    movies = [
        result(f"Film {i} (2026) 1080p x264 -YTS", "YTS", 0, YTS_CATS) for i in range(9)
    ]
    tv = [
        result(f"Series {i} S01E01 720p x264-GRP", "The Pirate Bay", 2, TPB_TV_CATS)
        for i in range(3)
    ]
    refresh(db_session, FakeProvider([anime + movies + tv]))
    # The strict anime wall hides unknown-count shows, and every test in this
    # file is about wall MECHANICS — paging, dedupe, the picker — not the
    # strict rule (pinned in test_anime_films_rail.py). Mark the anime works
    # complete so the mechanics stay observable.
    from miru.catalog.models import CatalogWork

    for w in db_session.query(CatalogWork).filter(CatalogWork.kind.in_(("anime", "series"))):
        w.episode_count = 1
        w.episodes_covered = 1
        w.release_status = "FINISHED"
    db_session.commit()
    return db_session




def _make_visible(db):
    """The strict anime wall hides unknown-count shows; these tests are about
    the picker, so their work is marked complete to stay on the wall."""
    from miru.catalog.models import CatalogWork

    for w in db.query(CatalogWork).filter(CatalogWork.kind.in_(("anime", "series"))):
        w.episode_count = 1
        w.episodes_covered = 1
        w.release_status = "FINISHED"
    db.commit()


class TestTheWall:
    def test_one_request_returns_the_whole_screen(self, client, filled):
        b = client.get("/api/catalog").json()
        assert [r["key"] for r in b["rails"]] == ["latest", "trending"]
        assert b["empty"] is False
        assert b["refreshed_at"] is not None

    def test_rails_never_show_the_same_work_twice(self, client, filled):
        # The catalog is small. Drawn independently, Trending and Fresh would
        # be largely the same titles in a different order.
        b = client.get("/api/catalog").json()
        ids = [w["id"] for rail in b["rails"] for w in rail["items"]]
        assert len(ids) == len(set(ids))

    def test_filtering_by_kind_excludes_the_others(self, client, filled):
        b = client.get("/api/catalog?kind=anime").json()
        kinds = {w["kind"] for rail in b["rails"] for w in rail["items"]}
        assert kinds == {"anime"}

    def test_an_unknown_kind_is_rejected(self, client):
        assert client.get("/api/catalog?kind=documentary").status_code == 422

    def test_a_first_run_says_it_is_empty_rather_than_looking_broken(self, client, db_session):
        b = client.get("/api/catalog").json()
        assert b["empty"] is True
        assert all(r["layout"] == "empty" for r in b["rails"])

    def test_the_wall_reports_when_the_pc_cannot_take_a_download(self, client, filled):
        # The wall is served from the laptop, aria2 lives on the PC. Without
        # this the page looks healthy while every Download button 502s.
        assert client.get("/api/catalog").json()["pc_reachable"] is False

    def test_freshness_and_failure_are_visible_on_the_wall(self, client, db_session):
        refresh(db_session, FakeProvider([RuntimeError("Prowlarr unreachable")]))
        b = client.get("/api/catalog").json()
        assert "unreachable" in b["refresh_error"]


class TestSparseRails:
    def test_a_short_row_renders_as_a_grid_not_a_rail(self, client, filled):
        # 3 TV works: a rail shows all three and 60% empty track, which reads as
        # a rendering failure.
        b = client.get("/api/catalog?kind=series").json()
        populated = [r for r in b["rails"] if r["items"]]
        assert populated and all(r["layout"] == "grid" for r in populated)

    def test_a_full_row_renders_as_a_rail(self, client, filled):
        b = client.get("/api/catalog?kind=anime").json()
        # The first rail gets first pick of the catalogue, so it is the one with
        # enough to fill a rail; later rails see only what it left.
        assert b["rails"][0]["layout"] == "rail"

    def test_a_thin_kind_explains_itself(self, client, filled):
        note = client.get("/api/catalog?kind=series").json()["note"]
        assert note and "Prowlarr" in note

    def test_a_healthy_kind_does_not_apologise(self, client, filled):
        assert client.get("/api/catalog?kind=anime").json()["note"] is None


class TestPaging:
    def test_a_rail_pages_with_a_cursor_rather_than_an_offset(self, client, filled):
        first = client.get("/api/catalog/rail/latest?kind=anime").json()
        assert first["items"]
        if first["next_cursor"]:
            second = client.get(
                f"/api/catalog/rail/latest?kind=anime&cursor={first['next_cursor']}"
            ).json()
            a = {w["id"] for w in first["items"]}
            b = {w["id"] for w in second["items"]}
            assert not (a & b)

    def test_an_unreadable_cursor_starts_over_rather_than_erroring(self, client, filled):
        # The user did not type it, so a stale cursor from an older shape is not
        # their problem.
        r = client.get("/api/catalog/rail/latest?cursor=not-a-real-cursor")
        assert r.status_code == 200 and r.json()["items"]

    def test_an_unknown_rail_is_a_404(self, client, filled):
        assert client.get("/api/catalog/rail/nonsense").status_code == 404


class TestTheReleasePicker:
    def test_a_work_offers_three_named_choices(self, client, filled, db_session):
        wid = client.get("/api/catalog?kind=anime").json()["rails"][0]["items"][0]["id"]
        b = client.get(f"/api/catalog/works/{wid}").json()
        assert set(b["choices"]) == {"best", "smallest", "best_quality"}
        assert b["choices"]["best"] is not None

    def test_every_release_says_whether_it_wakes_the_pc(self, client, db_session):
        refresh(db_session, FakeProvider([[
            result("[Grp] Show - 01 [1080p][x265][10bit]", "Nyaa.si", 90, NYAA_CATS),
        ]]))
        _make_visible(db_session)
        wid = client.get("/api/catalog").json()["rails"][0]["items"][0]["id"]
        rel = client.get(f"/api/catalog/works/{wid}").json()["releases"][0]
        assert rel["needs_pc"] is True

    def test_a_dead_swarm_is_declared_up_front(self, client, db_session):
        refresh(db_session, FakeProvider([[
            result("[Grp] Show - 01 [1080p][x264]", "Nyaa.si", 1, NYAA_CATS),
        ]]))
        _make_visible(db_session)
        wid = client.get("/api/catalog").json()["rails"][0]["items"][0]["id"]
        assert client.get(f"/api/catalog/works/{wid}").json()["all_dead"] is True

    def test_an_unknown_work_is_a_404(self, client, filled):
        assert client.get("/api/catalog/works/999999").status_code == 404


class TestStartingADownload:
    def test_a_sleeping_pc_refuses_rather_than_failing_obscurely(self, client, filled, monkeypatch):
        import miru.catalog.router as mod

        monkeypatch.setattr(mod, "configured", lambda: True)
        wid = client.get("/api/catalog").json()["rails"][0]["items"][0]["id"]
        r = client.post(f"/api/catalog/works/{wid}/download", json={})
        assert r.status_code == 503
        assert "asleep" in r.json()["detail"].lower()

    def test_never_installing_a_downloader_says_so_instead_of_blaming_the_pc(
        self, client, filled
    ):
        # A lesson already learned once with the transcode worker: "the PC is
        # asleep" and "you never set this up" are different problems, and
        # sending someone to wake a machine that is already awake wastes their
        # evening.
        wid = client.get("/api/catalog").json()["rails"][0]["items"][0]["id"]
        r = client.post(f"/api/catalog/works/{wid}/download", json={})
        assert r.status_code == 503
        detail = r.json()["detail"].lower()
        assert "set up" in detail or "install" in detail
        assert "asleep" not in detail

    def test_the_wall_tells_the_two_apart(self, client, filled):
        b = client.get("/api/catalog").json()
        assert b["downloader_configured"] is False
        assert b["pc_reachable"] is False

    def test_downloading_with_no_choice_picks_for_the_user(self, client, filled, monkeypatch):
        import miru.catalog.router as mod

        fake = FakeDownloader()
        monkeypatch.setattr(mod, "pc_reachable", lambda: True)
        monkeypatch.setattr(mod, "configured", lambda: True)
        monkeypatch.setattr(mod, "downloader", lambda: fake)

        wid = client.get("/api/catalog").json()["rails"][0]["items"][0]["id"]
        b = client.post(f"/api/catalog/works/{wid}/download", json={}).json()
        assert b["job_id"] == "gid1"
        assert b["release"]["grabbable"] is True

    def test_the_grab_is_built_from_the_infohash_not_the_stored_link(
        self, client, filled, monkeypatch
    ):
        # Prowlarr re-encrypts its links every response, so the one on the row
        # may already be dead by the time anyone clicks.
        import miru.catalog.router as mod

        fake = FakeDownloader()
        monkeypatch.setattr(mod, "pc_reachable", lambda: True)
        monkeypatch.setattr(mod, "configured", lambda: True)
        monkeypatch.setattr(mod, "downloader", lambda: fake)

        wid = client.get("/api/catalog").json()["rails"][0]["items"][0]["id"]
        client.post(f"/api/catalog/works/{wid}/download", json={})
        magnet, _ = fake.submitted[0]
        assert magnet.startswith("magnet:?xt=urn:btih:")
        assert "&tr=" in magnet          # trackers, so peers arrive in seconds

    def test_watch_now_asks_for_sequential_pieces_and_download_does_not(
        self, client, filled, monkeypatch
    ):
        # The whole point of the downloader swap: intent decides piece order.
        import miru.catalog.router as mod

        fake = FakeDownloader()
        monkeypatch.setattr(mod, "pc_reachable", lambda: True)
        monkeypatch.setattr(mod, "configured", lambda: True)
        monkeypatch.setattr(mod, "downloader", lambda: fake)
        monkeypatch.setattr(mod, "supports_streaming", lambda: True)

        wid = client.get("/api/catalog").json()["rails"][0]["items"][0]["id"]
        client.post(f"/api/catalog/works/{wid}/download", json={"watch": True})
        client.post(f"/api/catalog/works/{wid}/download", json={"watch": False})
        assert [seq for _, seq in fake.submitted] == [True, False]

    def test_a_backend_that_cannot_stream_says_so_rather_than_promising(
        self, client, filled, monkeypatch
    ):
        # With aria2 configured, Watch Now can only mean "when it finishes", and
        # the response has to admit that so the UI does not promise otherwise.
        import miru.catalog.router as mod

        fake = FakeDownloader()
        monkeypatch.setattr(mod, "pc_reachable", lambda: True)
        monkeypatch.setattr(mod, "configured", lambda: True)
        monkeypatch.setattr(mod, "downloader", lambda: fake)
        monkeypatch.setattr(mod, "supports_streaming", lambda: False)

        wid = client.get("/api/catalog").json()["rails"][0]["items"][0]["id"]
        b = client.post(f"/api/catalog/works/{wid}/download", json={"watch": True}).json()
        assert b["streaming"] is False
        assert fake.submitted[0][1] is False

    def test_the_job_id_is_stored_so_a_reload_does_not_lose_it(self, client, filled, monkeypatch):
        import miru.catalog.router as mod

        fake = FakeDownloader()
        fake.submit = lambda magnet, **kw: type("J", (), {"id": "gid7"})()
        monkeypatch.setattr(mod, "pc_reachable", lambda: True)
        monkeypatch.setattr(mod, "configured", lambda: True)
        monkeypatch.setattr(mod, "downloader", lambda: fake)

        wid = client.get("/api/catalog").json()["rails"][0]["items"][0]["id"]
        client.post(f"/api/catalog/works/{wid}/download", json={})
        again = client.get(f"/api/catalog/works/{wid}").json()
        assert again["download_job_id"] == "gid7"


class TestPollingDownloads:
    def test_nothing_in_flight_is_an_empty_list_not_an_error(self, client, filled):
        assert client.get("/api/catalog/downloads").json()["downloads"] == []

    def test_one_request_covers_every_in_flight_grab(self, client, filled, monkeypatch):
        import miru.catalog.router as mod
        from miru.acquisition.provider import DownloadStatus

        fake = FakeDownloader(
            status=DownloadStatus(id="gid1", state="downloading", progress=0.41,
                                  speed_bps=8_200_000, eta_seconds=180)
        )
        monkeypatch.setattr(mod, "pc_reachable", lambda: True)
        monkeypatch.setattr(mod, "configured", lambda: True)
        monkeypatch.setattr(mod, "downloader", lambda: fake)
        wid = client.get("/api/catalog").json()["rails"][0]["items"][0]["id"]
        client.post(f"/api/catalog/works/{wid}/download", json={})
        b = client.get("/api/catalog/downloads").json()["downloads"]
        assert len(b) == 1
        assert b[0]["state"] == "downloading" and b[0]["progress"] == 0.41
        # aria2 finishing is not the same as playable — the mover still has to
        # promote it. Without this the card snaps back to Download.
        assert b[0]["in_library"] is False

    def test_a_job_the_downloader_forgot_does_not_break_the_whole_poll(
        self, client, filled, monkeypatch
    ):
        import miru.catalog.router as mod
        from miru.acquisition.provider import AcquisitionError

        fake = FakeDownloader(fail_status=AcquisitionError("no such gid"))
        monkeypatch.setattr(mod, "pc_reachable", lambda: True)
        monkeypatch.setattr(mod, "configured", lambda: True)
        monkeypatch.setattr(mod, "downloader", lambda: fake)
        wid = client.get("/api/catalog").json()["rails"][0]["items"][0]["id"]
        client.post(f"/api/catalog/works/{wid}/download", json={})
        b = client.get("/api/catalog/downloads").json()["downloads"]
        assert b[0]["state"] == "failed" and "no such gid" in b[0]["error"]


class TestTheSheetKnowsWhatIsOnDisk:
    def test_an_owned_episode_is_named_in_the_work_payload(self, client, db_session):
        """file-page §5, the half that was actually missing.

        The sheet grew its own episode list from indexer releases, so it offers
        to download an episode that is already in the library. The library
        already knows better — series.episodes_for merges owned and available —
        and the payload now carries the owned rows so a ✓ can replace a
        re-download.
        """
        from miru.catalog.models import CatalogRelease, CatalogWork
        from miru.library.models import MediaFile

        f = MediaFile(id=77, path="/m/Show.S01E05.mkv", title="Show 5",
                      size_bytes=1, mtime=0.0)
        db_session.add(f)
        w = CatalogWork(kind="anime", normalised_title="show", display_title="Show",
                        library_file_id=77)
        db_session.add(w)
        db_session.flush()
        db_session.add(CatalogRelease(
            info_hash="c" * 40, indexer="Nyaa.si", guid="g", title="Show - 05",
            kind="anime", work_id=w.id, parsed_title="Show", episode=5,
            seeder_pct=0.5, seeders=5, leechers=0, size_bytes=1, magnet="magnet:?x",
        ))
        db_session.commit()

        body = client.get(f"/api/catalog/works/{w.id}").json()
        assert body["owned_episodes"] == [
            {"episode": 5, "episode_end": None, "file_id": 77}
        ]

    def test_a_work_with_nothing_on_disk_owns_nothing(self, client, db_session):
        from miru.catalog.models import CatalogRelease, CatalogWork

        w = CatalogWork(kind="anime", normalised_title="new", display_title="New")
        db_session.add(w)
        db_session.flush()
        db_session.add(CatalogRelease(
            info_hash="d" * 40, indexer="Nyaa.si", guid="g", title="New - 01",
            kind="anime", work_id=w.id, parsed_title="New", episode=1,
            seeder_pct=0.5, seeders=5, leechers=0, size_bytes=1, magnet="magnet:?x",
        ))
        db_session.commit()
        assert client.get(f"/api/catalog/works/{w.id}").json()["owned_episodes"] == []


class TestTheCardSaysWhatItHolds:
    def test_the_item_payload_carries_the_coverage_verdict(self, client, db_session):
        from miru.catalog.models import CatalogWork

        w = CatalogWork(kind="anime", normalised_title="f", display_title="F",
                        release_count=3, best_seeder_pct=9.0, episode_count=24,
                        episodes_covered=24, release_status="FINISHED")
        db_session.add(w)
        db_session.commit()
        item = next(
            x for r in client.get("/api/catalog?kind=anime-series").json()["rails"]
            for x in r["items"] if x["id"] == w.id
        )
        assert item["complete"] is True
        assert item["episodes_covered"] == 24
        assert item["episodes_expected"] == 24

    def test_an_unknown_denominator_is_none_not_false(self, client, db_session):
        # False would render "incomplete" on a show nobody has counted.
        from miru.catalog.models import CatalogWork

        w = CatalogWork(kind="movie", normalised_title="u", display_title="U",
                        format="MOVIE", release_count=1, best_seeder_pct=1.0)
        db_session.add(w)
        db_session.commit()
        item = next(
            x for r in client.get("/api/catalog?kind=movie").json()["rails"]
            for x in r["items"] if x["id"] == w.id
        )
        assert item["complete"] is None

    def test_a_film_gets_no_episode_verdict_at_all(self, client, db_session):
        # AniList says a film has 1 episode and film releases carry no episode
        # number, so every film read "0 of 1" — incomplete arithmetic on a
        # thing that has no episodes. A film with a release IS complete;
        # the chip falls back to the release count.
        from miru.catalog.models import CatalogWork

        w = CatalogWork(kind="anime", normalised_title="yn", display_title="Your Name",
                        format="MOVIE", release_count=2, best_seeder_pct=5.0,
                        episode_count=1, episodes_covered=0)
        db_session.add(w)
        db_session.commit()
        item = next(
            x for r in client.get("/api/catalog?kind=anime-movies").json()["rails"]
            for x in r["items"] if x["id"] == w.id
        )
        assert item["complete"] is None
        assert item["episodes_expected"] is None
