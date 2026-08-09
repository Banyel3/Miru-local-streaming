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
