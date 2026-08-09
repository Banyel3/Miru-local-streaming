"""Use case: Watch Now streams; only Keep fills the library.

The user pressed Watch Now and later found the whole film in their library —
"it finished downloading even when I just clicked watch now". BitTorrent
fetches the entire file either way (sequential changes piece ORDER, not how
much), so true no-disk streaming over torrents does not exist in any client
worth using. What every practical tool does — Stremio included — is download to
disk and clean up after. That is what Watch Now is now: the disk is the buffer,
nothing joins the library unless the user says Keep, and the janitor deletes
what nobody kept.

Nothing here talks to qBittorrent or the filesystem mover for real; both are
faked at the seam.
"""

import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from miru.catalog.models import CatalogRelease, CatalogWork
from miru.library.incoming import promote


def _work(db, title="Show", job=None, ephemeral=False, **kw):
    w = CatalogWork(
        kind="anime", normalised_title=title.casefold(), display_title=title,
        download_job_id=job, ephemeral=ephemeral, **kw,
    )
    db.add(w)
    db.commit()
    return w


def _grabbable(db, work, info_hash="a" * 40):
    db.add(CatalogRelease(
        info_hash=info_hash, indexer="Nyaa.si", guid="g", title=f"{work.display_title} - 01",
        kind="anime", work_id=work.id, parsed_title=work.display_title,
        seeder_pct=0.9, seeders=50, leechers=0, size_bytes=1,
        magnet=f"magnet:?xt=urn:btih:{info_hash}",
    ))
    db.commit()


@pytest.fixture
def grabbing(client, db_session, monkeypatch):
    from miru.catalog import router as mod

    class FakeDl:
        def submit(self, magnet, sequential=False):
            class Job:
                id = "a" * 40
            return Job()

    monkeypatch.setattr(mod, "downloader", lambda: FakeDl())
    monkeypatch.setattr(mod, "configured", lambda: True)
    monkeypatch.setattr(mod, "pc_reachable", lambda: True)
    monkeypatch.setattr(mod, "supports_streaming", lambda: True)
    return client


class TestWatchNowIsEphemeralAndDownloadIsNot:
    def test_watch_now_marks_the_grab_ephemeral(self, grabbing, db_session):
        w = _work(db_session)
        _grabbable(db_session, w)
        grabbing.post(f"/api/catalog/works/{w.id}/download", json={"watch": True})
        db_session.refresh(w)
        assert w.ephemeral is True

    def test_download_is_a_keep(self, grabbing, db_session):
        w = _work(db_session)
        _grabbable(db_session, w)
        grabbing.post(f"/api/catalog/works/{w.id}/download", json={"watch": False})
        db_session.refresh(w)
        assert w.ephemeral is False

    def test_download_on_something_already_streaming_flips_it_to_keep(
        self, grabbing, db_session
    ):
        # The download is already running; pressing Download must not restart
        # it, just change what happens when it finishes.
        w = _work(db_session)
        _grabbable(db_session, w)
        grabbing.post(f"/api/catalog/works/{w.id}/download", json={"watch": True})
        grabbing.post(f"/api/catalog/works/{w.id}/download", json={"watch": False})
        db_session.refresh(w)
        assert w.ephemeral is False


class TestKeepPromotesOnTheNextPoll:
    def test_the_keep_route_clears_ephemeral(self, client, db_session):
        w = _work(db_session, job="b" * 40, ephemeral=True)
        res = client.post(f"/api/catalog/downloads/{'b' * 40}/keep")
        assert res.status_code == 200
        db_session.refresh(w)
        assert w.ephemeral is False

    def test_an_ephemeral_done_download_is_not_scanned_into_the_library(
        self, client, db_session, monkeypatch
    ):
        """The heart of the mode.

        The downloads poll promotes a finished download the moment it reports
        done. For an ephemeral work that promotion is exactly what must NOT
        happen — it is how Watch Now filled the library.
        """
        from miru.catalog import router as mod
        from miru.acquisition.provider import DownloadStatus

        w = _work(db_session, job="c" * 40, ephemeral=True)
        scans = []
        monkeypatch.setattr(mod, "_request_scan", lambda: scans.append(1))
        monkeypatch.setattr(mod, "pc_reachable", lambda: True)

        class FakeDl:
            def statuses(self):
                return {"c" * 40: DownloadStatus(
                    id="c" * 40, state="done", progress=1.0, name="Show",
                    downloaded_bytes=1, total_bytes=1, speed_bps=0,
                    eta_seconds=None, error=None,
                )}

        monkeypatch.setattr(mod, "downloader", lambda: FakeDl())
        client.get("/api/catalog/downloads")
        assert scans == [], "an ephemeral download was promoted into the library"

    def test_a_kept_done_download_is_still_promoted(self, client, db_session, monkeypatch):
        from miru.catalog import router as mod
        from miru.acquisition.provider import DownloadStatus

        w = _work(db_session, job="d" * 40, ephemeral=False)
        scans = []
        monkeypatch.setattr(mod, "_request_scan", lambda: scans.append(1))
        monkeypatch.setattr(mod, "_link_by_filename", lambda db, w: None)
        monkeypatch.setattr(mod, "pc_reachable", lambda: True)

        class FakeDl:
            def statuses(self):
                return {"d" * 40: DownloadStatus(
                    id="d" * 40, state="done", progress=1.0, name="Show",
                    downloaded_bytes=1, total_bytes=1, speed_bps=0,
                    eta_seconds=None, error=None,
                )}

        monkeypatch.setattr(mod, "downloader", lambda: FakeDl())
        client.get("/api/catalog/downloads")
        assert scans == [1]


class TestTheMoverLeavesEphemeralFilesAlone:
    def _settled(self, path):
        old = time.time() - 10_000
        for p in [path, *path.rglob("*")] if path.is_dir() else [path]:
            os.utime(p, (old, old))

    def test_an_ephemeral_file_is_not_promoted(self, tmp_path):
        inc, lib = tmp_path / "in", tmp_path / "lib"
        inc.mkdir(); lib.mkdir()
        f = inc / "Show.S01E01.mkv"
        f.write_bytes(b"x")
        self._settled(f)
        got = promote(inc, lib, skip={"Show.S01E01.mkv"})
        assert got["promoted"] == 0
        assert f.exists(), "deleted rather than left for the janitor"

    def test_everything_else_still_moves(self, tmp_path):
        inc, lib = tmp_path / "in", tmp_path / "lib"
        inc.mkdir(); lib.mkdir()
        keep = inc / "Kept.mkv"; keep.write_bytes(b"x")
        eph = inc / "Streamed.mkv"; eph.write_bytes(b"x")
        self._settled(keep); self._settled(eph)
        got = promote(inc, lib, skip={"Streamed.mkv"})
        assert got["promoted"] == 1
        assert (lib / "Kept.mkv").exists()


class TestTheJanitorCleansUpWhatNobodyKept:
    def test_an_idle_ephemeral_download_is_deleted_with_its_files(
        self, db_session, monkeypatch
    ):
        from miru.catalog import janitor as mod

        stale = datetime.now(timezone.utc) - timedelta(hours=30)
        w = _work(db_session, job="e" * 40, ephemeral=True, last_streamed_at=stale)

        calls = []

        class FakeDl:
            def cancel(self, job_id, delete_files=False):
                calls.append((job_id, delete_files))

        monkeypatch.setattr(mod, "downloader", lambda: FakeDl())
        monkeypatch.setattr(mod, "pc_reachable", lambda: True)
        mod.sweep_ephemeral(db_session)
        db_session.refresh(w)
        assert calls == [("e" * 40, True)]
        assert w.download_job_id is None

    def test_a_recently_watched_stream_is_left_alone(self, db_session, monkeypatch):
        from miru.catalog import janitor as mod

        fresh = datetime.now(timezone.utc) - timedelta(hours=1)
        _work(db_session, job="f" * 40, ephemeral=True, last_streamed_at=fresh)
        calls = []

        class FakeDl:
            def cancel(self, job_id, delete_files=False):
                calls.append(job_id)

        monkeypatch.setattr(mod, "downloader", lambda: FakeDl())
        monkeypatch.setattr(mod, "pc_reachable", lambda: True)
        mod.sweep_ephemeral(db_session)
        assert calls == []

    def test_a_kept_download_is_never_the_janitors_business(self, db_session, monkeypatch):
        from miru.catalog import janitor as mod

        stale = datetime.now(timezone.utc) - timedelta(days=9)
        _work(db_session, job="9" * 40, ephemeral=False, last_streamed_at=stale)
        calls = []

        class FakeDl:
            def cancel(self, job_id, delete_files=False):
                calls.append(job_id)

        monkeypatch.setattr(mod, "downloader", lambda: FakeDl())
        monkeypatch.setattr(mod, "pc_reachable", lambda: True)
        mod.sweep_ephemeral(db_session)
        assert calls == []

    def test_a_never_streamed_ephemeral_grab_ages_out_too(self, db_session, monkeypatch):
        # Watch Now pressed, page closed before a byte played. last_streamed_at
        # is null forever; the grab must still not live forever.
        from miru.catalog import janitor as mod

        w = _work(db_session, job="a1" + "0" * 38, ephemeral=True)
        w.first_seen_at = datetime.now(timezone.utc) - timedelta(days=3)
        db_session.commit()
        calls = []

        class FakeDl:
            def cancel(self, job_id, delete_files=False):
                calls.append(delete_files)

        monkeypatch.setattr(mod, "downloader", lambda: FakeDl())
        monkeypatch.setattr(mod, "pc_reachable", lambda: True)
        mod.sweep_ephemeral(db_session)
        assert calls == [True]

    def test_the_pc_being_asleep_defers_rather_than_forgets(self, db_session, monkeypatch):
        from miru.catalog import janitor as mod

        stale = datetime.now(timezone.utc) - timedelta(hours=30)
        w = _work(db_session, job="ab" + "0" * 38, ephemeral=True, last_streamed_at=stale)
        monkeypatch.setattr(mod, "pc_reachable", lambda: False)
        mod.sweep_ephemeral(db_session)
        db_session.refresh(w)
        # Still on the books — the next pass with the PC awake deletes it.
        assert w.download_job_id is not None


class TestCancelStillDefaultsToKeepingBytes:
    def test_the_default_is_unchanged(self, monkeypatch):
        # deleteFiles=false was a deliberate decision — cancelling a download
        # must not destroy a file the user may be mid-watch on. Only the
        # janitor opts into deletion.
        import miru.acquisition.qbittorrent as qb

        seen = []
        monkeypatch.setattr(qb, "_call", lambda path, params=None, **k: seen.append((path, params)) or "Ok.")
        qb.QBittorrentProvider().cancel("ABC")
        assert seen == [("/torrents/delete", {"hashes": "abc", "deleteFiles": "false"})]

    def test_the_janitor_can_opt_into_deletion(self, monkeypatch):
        import miru.acquisition.qbittorrent as qb

        seen = []
        monkeypatch.setattr(qb, "_call", lambda path, params=None, **k: seen.append((path, params)) or "Ok.")
        qb.QBittorrentProvider().cancel("ABC", delete_files=True)
        assert seen == [("/torrents/delete", {"hashes": "abc", "deleteFiles": "true"})]


class TestStreamingLeavesAHeartbeat:
    def test_serving_bytes_touches_last_streamed_at(self, db_session, monkeypatch):
        # The janitor ages against this; without the touch every stream looks
        # abandoned from the moment it starts.
        from miru.streaming import partial as mod

        monkeypatch.setattr(mod, "_touched", {})
        w = _work(db_session, job="beef" + "0" * 36, ephemeral=True)
        mod._touch_last_streamed("beef" + "0" * 36)
        db_session.expire_all()
        assert db_session.get(CatalogWork, w.id).last_streamed_at is not None

    def test_the_touch_is_throttled(self, db_session, monkeypatch):
        from miru.streaming import partial as mod

        monkeypatch.setattr(mod, "_touched", {})
        writes = []
        monkeypatch.setattr(mod, "SessionLocal", None, raising=False)

        real = mod._touch_last_streamed

        # First call writes; the second inside the window must not even open a
        # session — count via the throttle map.
        _work(db_session, job="feed" + "0" * 36, ephemeral=True)
        real("feed" + "0" * 36)
        stamp = dict(mod._touched)
        real("feed" + "0" * 36)
        assert mod._touched == stamp


class TestTheSkipListNamesWhatIsActuallyOnDisk:
    """Scary Movie was ephemeral and landed in the library anyway.

    The skip-list stored the TORRENT name; the on-disk entry was the folder
    `www.UIndex.org    -    Scary Movie…`. promote() compared entry names,
    never matched, and moved the stream into the library — the exact thing
    ephemeral exists to prevent. qBittorrent's own row names the real entry in
    `content_path`; that is what the poll must remember.
    """

    def test_the_poll_stores_the_content_name_not_the_torrent_name(
        self, client, db_session, monkeypatch
    ):
        from miru.catalog import router as mod
        from miru.acquisition.provider import DownloadStatus

        w = _work(db_session, job="aa" + "0" * 38, ephemeral=True)
        monkeypatch.setattr(mod, "pc_reachable", lambda: True)

        class FakeDl:
            def statuses(self):
                return {"aa" + "0" * 38: DownloadStatus(
                    id="aa" + "0" * 38, state="downloading", progress=0.4,
                    name="Scary Movie 2026 1080p WEBRip",
                    downloaded_bytes=1, total_bytes=2, speed_bps=1,
                    eta_seconds=None, error=None,
                    content_name="www.UIndex.org - Scary Movie 2026",
                )}

        monkeypatch.setattr(mod, "downloader", lambda: FakeDl())
        client.get("/api/catalog/downloads")
        db_session.refresh(w)
        assert w.download_name == "www.UIndex.org - Scary Movie 2026"

    def test_qbittorrent_reports_the_content_basename(self, monkeypatch):
        import miru.acquisition.qbittorrent as qb

        monkeypatch.setattr(qb, "_json", lambda p, q=None: [{
            "hash": "bb" + "0" * 38, "state": "downloading", "progress": 0.4,
            "size": 2, "completed": 1, "dlspeed": 1, "eta": 60,
            "name": "Scary Movie 2026 1080p WEBRip",
            "content_path": "/mnt/incoming/www.UIndex.org - Scary Movie 2026",
        }])
        s = qb.QBittorrentProvider().statuses()["bb" + "0" * 38]
        assert s.content_name == "www.UIndex.org - Scary Movie 2026"

    def test_a_backend_without_content_paths_falls_back_to_the_name(
        self, client, db_session, monkeypatch
    ):
        from miru.catalog import router as mod
        from miru.acquisition.provider import DownloadStatus

        w = _work(db_session, job="cc" + "0" * 38, ephemeral=True)
        monkeypatch.setattr(mod, "pc_reachable", lambda: True)

        class FakeDl:
            def statuses(self):
                return {"cc" + "0" * 38: DownloadStatus(
                    id="cc" + "0" * 38, state="downloading", progress=0.4,
                    name="Plain.mkv", downloaded_bytes=1, total_bytes=2,
                    speed_bps=1, eta_seconds=None, error=None,
                )}

        monkeypatch.setattr(mod, "downloader", lambda: FakeDl())
        client.get("/api/catalog/downloads")
        db_session.refresh(w)
        assert w.download_name == "Plain.mkv"


class TestSearchGrabsGetTheSameLifecycle:
    """"Even if I just clicked watch now it places it in the library."

    The card path sets download_job_id and ephemeral on the work; the
    SEARCH-page path (acquisition/download) submitted the torrent and walked
    away — no link, no flag — so the poll's done-branch saw an ordinary
    download and promoted the stream. The release the user clicked carries the
    infohash, and ingest has already made a work for it: link them.
    """

    @pytest.fixture
    def grabbing(self, client, db_session, monkeypatch):
        import miru.acquisition.router as mod

        class FakeDl:
            def submit(self, magnet, sequential=False):
                class J:
                    id = "de" + "0" * 38
                return J()

        monkeypatch.setattr(mod, "downloader", lambda: FakeDl())
        monkeypatch.setattr(mod, "supports_streaming", lambda: True)
        return client

    def _release(self, db, ih):
        from miru.catalog.models import CatalogRelease

        w = _work(db, "Scary Movie")
        db.add(CatalogRelease(
            info_hash=ih, indexer="Knaben", guid="g", title="Scary Movie 2026",
            kind="movie", work_id=w.id, parsed_title="Scary Movie",
            seeder_pct=0.5, seeders=5, leechers=0, size_bytes=1,
            magnet=f"magnet:?xt=urn:btih:{ih}",
        ))
        db.commit()
        return w

    def test_a_search_watch_now_is_ephemeral(self, grabbing, db_session):
        ih = "de" + "0" * 38
        w = self._release(db_session, ih)
        grabbing.post("/api/acquisition/download", json={
            "result_id": f"magnet:?xt=urn:btih:{ih}", "info_hash": ih, "watch": True,
        })
        db_session.refresh(w)
        assert w.download_job_id == ih
        assert w.ephemeral is True

    def test_a_search_download_is_a_keep(self, grabbing, db_session):
        ih = "de" + "0" * 38
        w = self._release(db_session, ih)
        grabbing.post("/api/acquisition/download", json={
            "result_id": f"magnet:?xt=urn:btih:{ih}", "info_hash": ih, "watch": False,
        })
        db_session.refresh(w)
        assert w.ephemeral is False
        assert w.download_job_id == ih

    def test_a_result_the_catalogue_has_never_seen_still_downloads(
        self, grabbing, db_session
    ):
        # No release row → nothing to link, and that must not break the grab.
        res = grabbing.post("/api/acquisition/download", json={
            "result_id": "magnet:?xt=urn:btih:" + "ff" + "0" * 38, "watch": True,
        })
        assert res.status_code == 200


class TestTheSkipListDoesNotDependOnAnOpenBrowserTab:
    """The organic hole behind the recurring promotion bug.

    download_name is learned by the downloads POLL — which only runs while a
    page is open. Watch Now, close the tab, let the download finish: no poll
    ever learns the name, the skip-list stays empty, and the periodic scan
    promotes the stream into the library. The scan now asks the downloader
    directly for the on-disk names of ephemeral works, keyed by the hash known
    since grab time.
    """

    def test_the_scan_fills_missing_names_from_the_downloader(
        self, db_session, monkeypatch
    ):
        from miru.library import scanner as mod
        from miru.acquisition.provider import DownloadStatus

        w = _work(db_session, job="e1" + "0" * 38, ephemeral=True)  # no name
        named = _work(db_session, "Named", job="e2" + "0" * 38, ephemeral=True,
                      download_name="Known.mkv")

        class FakeDl:
            def statuses(self):
                return {"e1" + "0" * 38: DownloadStatus(
                    id="e1" + "0" * 38, state="downloading", progress=0.4,
                    name="Torrent Title", downloaded_bytes=1, total_bytes=2,
                    speed_bps=1, eta_seconds=None, error=None,
                    content_name="www.SomeSite - Real Folder Name",
                )}

        monkeypatch.setattr(mod, "_downloader_statuses", lambda: FakeDl().statuses())
        names = mod._ephemeral_names(db_session)
        assert "www.SomeSite - Real Folder Name" in names
        assert "Known.mkv" in names

    def test_a_sleeping_pc_still_yields_the_names_already_known(
        self, db_session, monkeypatch
    ):
        from miru.library import scanner as mod

        _work(db_session, "Named", job="e3" + "0" * 38, ephemeral=True,
              download_name="Known.mkv")
        monkeypatch.setattr(mod, "_downloader_statuses",
                            lambda: (_ for _ in ()).throw(RuntimeError("asleep")))
        assert mod._ephemeral_names(db_session) == {"Known.mkv"}
