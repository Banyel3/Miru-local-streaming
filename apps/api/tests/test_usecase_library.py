"""Use cases: film and TV, browsing, and keeping the library in step with disk.

The film side differs from anime mainly in what arrives: MP4 web-dls that direct
play, and remuxes carrying 5.1 audio the browser cannot decode.
"""

import time

from miru.library.incoming import is_settled, promote
from miru.library.scanner import scan
from miru.transcode.strategy import Probe, resolve_strategy
from tests.conftest import make_file


class TestFilmStrategies:
    def test_a_web_dl_plays_directly(self):
        assert resolve_strategy(
            Probe(container="mp4", video_codec="h264", audio_codec="aac", audio_channels=2)
        ) == "direct"

    def test_a_bluray_remux_with_surround_needs_the_audio_downmixed(self):
        for codec in ("ac3", "eac3", "dts", "truehd"):
            assert resolve_strategy(
                Probe(container="mkv", video_codec="h264", audio_codec=codec, audio_channels=6)
            ) == "transcode_audio", codec

    def test_a_4k_hevc_release_needs_the_gpu(self):
        assert resolve_strategy(
            Probe(container="mkv", video_codec="hevc", audio_codec="eac3",
                  audio_channels=6, width=3840, height=2160)
        ) == "transcode_full"

    def test_webm_plays_directly(self):
        assert resolve_strategy(
            Probe(container="webm", video_codec="vp9", audio_codec="opus", audio_channels=2)
        ) == "direct"

    def test_an_unprobed_file_is_optimistic_rather_than_burning_gpu(self):
        # A wrong 'direct' costs one failed play; a wrong 'transcode_full' costs
        # GPU time on every request for a file that never needed it.
        assert resolve_strategy(Probe()) == "direct"


class TestBrowsing:
    def test_the_library_lists_everything(self, client, db_session):
        for i in range(5):
            make_file(db_session, path=f"/m/f{i}.mp4", title=f"Film {i}")
        assert len(client.get("/api/library").json()) == 5

    def test_search_matches_title_and_path(self, client, db_session):
        make_file(db_session, path="/m/Arrival.2016.mp4", title="Arrival.2016")
        make_file(db_session, path="/m/Dune.2021.mp4", title="Dune.2021")

        assert len(client.get("/api/library?q=arrival").json()) == 1
        assert len(client.get("/api/library?q=2021").json()) == 1
        assert len(client.get("/api/library?q=nothinghere").json()) == 0

    def test_sorting_by_title_is_alphabetical(self, client, db_session):
        for t in ("Zulu", "Alpha", "Mike"):
            make_file(db_session, path=f"/m/{t}.mp4", title=t)
        titles = [f["title"] for f in client.get("/api/library?sort=title").json()]
        assert titles == ["Alpha", "Mike", "Zulu"]

    def test_file_detail_carries_the_facts_the_ui_shows(self, client, db_session):
        f = make_file(db_session, container="mkv", video_codec="h264",
                      audio_codec="ac3", audio_channels=6, width=1920, height=1080)
        b = client.get(f"/api/files/{f.id}").json()
        assert (b["container"], b["video_codec"], b["audio_channels"]) == ("mkv", "h264", 6)
        assert (b["width"], b["height"]) == (1920, 1080)

    def test_missing_file_detail_is_a_404(self, client):
        assert client.get("/api/files/9999").status_code == 404


class TestScanningKeepsUpWithDisk:
    def _video(self, root, name, size=2048):
        root.mkdir(parents=True, exist_ok=True)
        f = root / name
        f.write_bytes(b"\0" * size)
        return f

    def test_new_files_are_added_and_unchanged_ones_are_not_reprobed(self, db_session, tmp_path):
        lib = tmp_path / "media"
        self._video(lib, "One.mp4")
        assert scan(db_session, [lib])["added"] == 1

        # Second scan: nothing changed, so nothing should be touched. This is
        # what makes repeat scans cheap over a slow mount.
        again = scan(db_session, [lib])
        assert again["unchanged"] == 1 and again["added"] == 0

    def test_a_modified_file_is_reprobed(self, db_session, tmp_path):
        lib = tmp_path / "media"
        f = self._video(lib, "One.mp4")
        scan(db_session, [lib])

        f.write_bytes(b"\0" * 4096)  # size changed
        assert scan(db_session, [lib])["updated"] == 1

    def test_a_deleted_file_leaves_the_library(self, db_session, tmp_path):
        lib = tmp_path / "media"
        f = self._video(lib, "Gone.mp4")
        scan(db_session, [lib])
        f.unlink()
        assert scan(db_session, [lib])["removed"] == 1

    def test_non_video_files_are_ignored(self, db_session, tmp_path):
        lib = tmp_path / "media"
        lib.mkdir()
        (lib / "poster.jpg").write_bytes(b"x")
        (lib / "notes.txt").write_bytes(b"x")
        (lib / "Movie.mkv").write_bytes(b"x")
        assert scan(db_session, [lib])["added"] == 1

    def test_nested_season_folders_are_walked(self, db_session, tmp_path):
        lib = tmp_path / "media"
        self._video(lib / "Show" / "Season 1", "E01.mkv")
        self._video(lib / "Show" / "Season 2", "E01.mkv")
        assert scan(db_session, [lib])["added"] == 2


class TestPromotingDownloads:
    """`incoming/` is not scanned. A growing file probes as garbage and, because
    the scan also writes size and mtime, would never be re-probed."""

    def _aged(self, path, seconds):
        import os
        t = time.time() - seconds
        os.utime(path, (t, t))

    def test_a_finished_download_is_promoted_and_indexed_in_one_scan(self, db_session, tmp_path):
        inc, lib = tmp_path / "incoming", tmp_path / "media"
        inc.mkdir(); lib.mkdir()
        f = inc / "Film.2024.1080p.mkv"; f.write_bytes(b"\0" * 2048)
        self._aged(f, 300)

        from miru.core.config import settings
        settings.incoming_path = str(inc)
        try:
            result = scan(db_session, [lib])
        finally:
            settings.incoming_path = ""

        assert result["promoted"] == 1 and result["added"] == 1
        assert (lib / "Film.2024.1080p.mkv").exists()

    def test_an_in_flight_download_is_left_alone(self, tmp_path):
        inc, lib = tmp_path / "incoming", tmp_path / "media"
        inc.mkdir(); lib.mkdir()
        (inc / "Downloading.mkv").write_bytes(b"x")   # just written
        assert promote(inc, lib, settle_seconds=120) == {"promoted": 0, "waiting": 1, "skipped": 0, "names": []}

    def test_an_aria2_control_file_marks_the_whole_entry_unfinished(self, tmp_path):
        # aria2 leaves .aria2 beside an unfinished download and removes it on
        # completion. That is a real signal and outranks the stillness guess.
        inc = tmp_path / "incoming"; inc.mkdir()
        part = inc / "Film.mkv.aria2"; part.write_bytes(b"x")
        self._aged(part, 9999)
        assert not is_settled(part, settle_seconds=120)

    def test_a_release_folder_waits_on_its_newest_file(self, tmp_path):
        inc = tmp_path / "incoming"; inc.mkdir()
        rel = inc / "Some.Release"; rel.mkdir()
        (rel / "video.mkv").write_bytes(b"x")
        self._aged(rel / "video.mkv", 300)
        assert is_settled(rel, settle_seconds=120)

        (rel / "sample.mkv").write_bytes(b"x")   # still arriving
        assert not is_settled(rel, settle_seconds=120)

    def test_promotion_never_overwrites_an_existing_library_entry(self, tmp_path):
        inc, lib = tmp_path / "incoming", tmp_path / "media"
        inc.mkdir(); lib.mkdir()
        (lib / "Film.mkv").write_bytes(b"original")
        dup = inc / "Film.mkv"; dup.write_bytes(b"new")
        self._aged(dup, 300)

        assert promote(inc, lib, settle_seconds=120)["promoted"] == 0
        assert (lib / "Film.mkv").read_bytes() == b"original"


class TestScanJobs:
    def test_a_scan_is_a_job_the_ui_can_poll(self, client):
        job = client.post("/api/library/scan").json()
        assert job["type"] == "library.scan"
        assert client.get(f"/api/jobs/{job['id']}").status_code == 200

    def test_unknown_job_is_a_404(self, client):
        assert client.get("/api/jobs/9999").status_code == 404


class TestFinishedDownloadsActuallyLand:
    """Promotion lives inside scan(), and for a while the only thing that ever
    called scan() was the Scan button in Settings. So a finished download sat in
    `incoming` indefinitely while its card said "Adding to your library…"."""

    def test_a_completed_job_asks_for_a_scan(self, client, db_session, monkeypatch):
        import miru.catalog.router as mod
        from miru.acquisition.provider import DownloadStatus
        from miru.catalog.models import CatalogWork

        asked = []
        monkeypatch.setattr(mod, "_request_scan", lambda: asked.append(1))
        monkeypatch.setattr(mod, "pc_reachable", lambda: True)
        monkeypatch.setattr(mod, "configured", lambda: True)
        monkeypatch.setattr(
            mod,
            "downloader",
            lambda: type("D", (), {"status": lambda self, j: DownloadStatus(
                id=j, state="done", progress=1.0)})(),
        )

        db_session.add(CatalogWork(kind="anime", normalised_title="x", display_title="X",
                                   genres=[], download_job_id="abc"))
        db_session.commit()

        client.get("/api/catalog/downloads")
        assert asked, "a finished download must trigger promotion, not wait 30 minutes"

    def test_an_in_flight_job_does_not(self, client, db_session, monkeypatch):
        import miru.catalog.router as mod
        from miru.acquisition.provider import DownloadStatus
        from miru.catalog.models import CatalogWork

        asked = []
        monkeypatch.setattr(mod, "_request_scan", lambda: asked.append(1))
        monkeypatch.setattr(mod, "pc_reachable", lambda: True)
        monkeypatch.setattr(mod, "configured", lambda: True)
        monkeypatch.setattr(
            mod,
            "downloader",
            lambda: type("D", (), {"status": lambda self, j: DownloadStatus(
                id=j, state="downloading", progress=0.4)})(),
        )
        db_session.add(CatalogWork(kind="anime", normalised_title="y", display_title="Y",
                                   genres=[], download_job_id="def"))
        db_session.commit()

        client.get("/api/catalog/downloads")
        assert not asked
