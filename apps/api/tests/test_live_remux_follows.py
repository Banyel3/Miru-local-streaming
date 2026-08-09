"""Use case: watching an MKV while it downloads.

The remux cache key included the exact completed-prefix length. On a growing
file that changes every second, so every request was a guaranteed miss, every
miss started a fresh gigabyte-scale ffmpeg, and the remux that finished was
keyed to a prefix that had already moved on. Measured on a live 3.8 GB download:
six requests over two minutes, all 503, while a complete 1.34 GB remux sat on
disk unserved and a second ffmpeg was already redoing it.

The first test here is the one that would have caught it, and the one I did not
write: two requests at different prefixes must start ONE ffmpeg between them.
"""

from pathlib import Path

import pytest

from miru.streaming import remux

# Imported at module scope on purpose: models register on Base at import time,
# and the db_session fixture's create_all only makes tables for what is already
# registered. Importing these inside a test body means "no such table" for
# whichever test file happens to run first.
from miru.catalog.models import CatalogWork  # noqa: E402
from miru.library.models import MediaFile  # noqa: E402


@pytest.fixture(autouse=True)
def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(remux, "_running", {})
    monkeypatch.setattr(remux, "_failed", {})
    monkeypatch.setattr(remux.settings, "remux_cache_path", str(tmp_path / "cache"))
    monkeypatch.setattr(remux.shutil, "which", lambda _: "/usr/bin/ffmpeg")


class TestAGrowingFileIsRemuxedOnce:
    def test_two_requests_at_different_prefixes_start_one_ffmpeg(
        self, monkeypatch, tmp_path
    ):
        """THE test. Its absence is why this shipped.

        A player polls while it buffers, and each poll sees a longer completed
        prefix. Keyed on that prefix, every poll was a different cache entry and
        so a different job.
        """
        import threading

        started, hold = [], threading.Event()

        def slow(*a, **k):
            # A real remux runs for minutes. A fake that returns instantly would
            # let the next call legitimately start a new job and hide the bug.
            started.append(a)
            hold.wait(5)

        monkeypatch.setattr(remux, "_run", slow)
        src = tmp_path / "show.mkv"
        src.write_bytes(b"x" * 4096)
        try:
            remux.ensure(1, src, prefix_bytes=1_000)
            remux.ensure(1, src, prefix_bytes=2_000)
            remux.ensure(1, src, prefix_bytes=3_000)
            assert len(started) == 1, f"started {len(started)} remuxes for one file"
        finally:
            hold.set()

    def test_a_failed_remux_is_not_retried_on_every_poll(self, monkeypatch, tmp_path):
        # The same runaway shape by another route: a source ffmpeg cannot read
        # would start a fresh process for every request the player makes.
        started = []
        monkeypatch.setattr(remux, "_run", lambda *a, **k: started.append(a))
        src = tmp_path / "bad.mkv"
        src.write_bytes(b"x" * 10)

        remux._failed[remux._key(1)] = "ffmpeg exited 1"
        for _ in range(4):
            assert remux.ensure(1, src, prefix_bytes=10) == "failed"
        assert started == []

    def test_a_finished_remux_is_served_rather_than_remade(self, monkeypatch, tmp_path):
        # The 1.34 GB file that existed on disk and was never served.
        src = tmp_path / "show.mkv"
        src.write_bytes(b"x" * 4096)
        remux.cached_path(1, src).write_bytes(b"done")

        started = []
        monkeypatch.setattr(remux, "_run", lambda *a, **k: started.append(a))
        assert remux.ensure(1, src, prefix_bytes=9_999) == "ready"
        assert started == []

    def test_the_cache_path_does_not_move_as_the_prefix_grows(self, tmp_path):
        src = tmp_path / "show.mkv"
        src.write_bytes(b"x" * 4096)
        assert remux.cached_path(1, src, prefix_bytes=10) == remux.cached_path(
            1, src, prefix_bytes=99_999
        )

    def test_a_different_file_still_gets_its_own_remux(self, monkeypatch, tmp_path):
        started = []
        monkeypatch.setattr(remux, "_run", lambda *a, **k: started.append(a))
        a = tmp_path / "a.mkv"; a.write_bytes(b"x" * 10)
        b = tmp_path / "b.mkv"; b.write_bytes(b"y" * 10)
        remux.ensure(1, a, prefix_bytes=10)
        remux.ensure(2, b, prefix_bytes=10)
        assert len(started) == 2

    def test_replacing_the_source_invalidates_the_cache(self, tmp_path):
        # The reason size and mtime are in the key at all: a different file
        # under the same name must not be served the old remux forever.
        src = tmp_path / "show.mkv"
        src.write_bytes(b"x" * 10)
        first = remux.cached_path(1, src)
        src.write_bytes(b"y" * 999)
        assert remux.cached_path(1, src) != first


class TestNothingHalfWrittenSurvives:
    def test_an_orphaned_part_file_is_cleaned_up(self, tmp_path):
        # One was left on disk by the runaway loop, at 1.17 GB.
        d = remux.cache_dir()
        (d / "abc.part.mp4").write_bytes(b"x" * 100)
        (d / "keep.mp4").write_bytes(b"x" * 100)
        remux.reap()
        assert not (d / "abc.part.mp4").exists()
        assert (d / "keep.mp4").exists()

    def test_a_part_file_being_written_right_now_is_left_alone(self, tmp_path):
        # Reaping the output of a live remux would kill the thing it is doing.
        d = remux.cache_dir()
        live = d / "live.part.mp4"
        live.write_bytes(b"x" * 100)
        remux._running[1] = _Alive()
        remux._active_parts.add(live)
        remux.reap()
        assert live.exists()


class _Alive:
    def is_alive(self):
        return True


class TestItFollowsTheDownloadInsteadOfRestarting:
    def test_the_feed_waits_for_more_bytes_while_the_file_grows(self, tmp_path):
        """One ffmpeg for the whole download, not one per prefix.

        Re-reading 1.4 GB from the start every few minutes to gain thirty
        seconds of video is the wrong shape — and it is what a per-prefix key
        forced. Following means the bytes are handed over once, as they arrive.
        """
        src = tmp_path / "growing.mkv"
        src.write_bytes(b"A" * 100)
        ceiling = {"n": 100}

        got = bytearray()
        for chunk in remux.follow(src, lambda: ceiling["n"], alive=lambda: len(got) < 300):
            got += chunk
            if len(got) == 100:  # more of the download lands
                src.write_bytes(b"A" * 100 + b"B" * 200)
                ceiling["n"] = 300
        assert bytes(got) == b"A" * 100 + b"B" * 200

    def test_it_stops_when_the_download_stops(self, tmp_path):
        # Otherwise the generator blocks forever and the ffmpeg it feeds never
        # finishes, holding a process per cancelled download.
        src = tmp_path / "stalled.mkv"
        src.write_bytes(b"A" * 50)
        out = b"".join(remux.follow(src, lambda: 50, alive=lambda: False))
        assert out == b"A" * 50

    def test_it_never_reads_past_the_completed_prefix(self, tmp_path):
        # The ceiling is the whole correctness argument: past it a sequential
        # torrent file is a hole, and a hole reads as zeros.
        src = tmp_path / "holed.mkv"
        src.write_bytes(b"A" * 100 + b"\0" * 900)
        out = b"".join(remux.follow(src, lambda: 100, alive=lambda: False))
        assert out == b"A" * 100


class TestTheEndpointServesTheGrowingRemux:
    def _wire(self, monkeypatch, tmp_path, ready_bytes=4096, complete=False):
        from miru.streaming import partial as mod

        src = tmp_path / "show.mkv"
        src.write_bytes(b"\0" * 8192)
        prefix = {
            "info_hash": "abc", "name": src.name, "file": src.name, "size_bytes": 8192,
            "playable_bytes": ready_bytes, "progress": 1.0 if complete else 0.4,
            "complete": complete, "sequential": True, "save_path": "", "content_path": "",
        }

        class Fake:
            def playable_prefix(self, h):
                return prefix

        monkeypatch.setattr(mod, "downloader", lambda: Fake())
        monkeypatch.setattr(mod, "supports_streaming", lambda: True)
        monkeypatch.setattr(mod, "_local_path", lambda p: src)
        return src

    def test_the_ceiling_is_the_remuxs_own_size_not_the_sources(
        self, client, monkeypatch, tmp_path
    ):
        """A remux is not the same length as its source.

        Dropping subtitles and changing container makes it smaller, and it is
        still being written. Using the source's completed prefix as the ceiling
        would promise bytes the remux does not have, and the read would come up
        short under a Content-Length already sent.
        """
        from miru.streaming import remux

        src = self._wire(monkeypatch, tmp_path, ready_bytes=8192)
        out = remux.cached_path(remux_id := __import__("miru.streaming.partial", fromlist=["x"]).info_hash_key("abc"), src)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"M" * 500)
        monkeypatch.setattr(remux, "ensure", lambda *a, **k: "ready")

        res = client.get("/api/stream/live/abc")
        assert res.status_code == 206
        assert res.headers["content-length"] == "500"
        assert remux_id  # the key is derived from the infohash, not a file row

    def test_a_remux_with_no_bytes_yet_is_a_wait_not_a_failure(
        self, client, monkeypatch, tmp_path
    ):
        from miru.streaming import remux

        self._wire(monkeypatch, tmp_path)
        monkeypatch.setattr(remux, "ensure", lambda *a, **k: "working")
        res = client.get("/api/stream/live/abc")
        assert res.status_code == 503
        assert "retry-after" in {k.lower() for k in res.headers}

    def test_the_downloader_decides_when_following_stops(
        self, client, monkeypatch, tmp_path
    ):
        # `alive` is what ends the follow. If the endpoint never passes one, a
        # cancelled download holds an ffmpeg forever.
        from miru.streaming import remux

        seen = {}
        self._wire(monkeypatch, tmp_path)
        monkeypatch.setattr(
            remux, "ensure",
            lambda *a, **k: (seen.update(k), "working")[1],
        )
        client.get("/api/stream/live/abc")
        assert callable(seen.get("alive")), seen


class TestWhenTheDownloadFinishesTheWatchPageGoesSomewhere:
    """Watch Now is a promise about a film, not about a torrent.

    Once the download completes, the mover promotes it out of incoming and the
    live stream correctly no longer has it — `_local_path` returns None and the
    page 404s. The user pressed Watch Now and, at the moment the film finally
    became fully watchable, was shown an error.
    """

    def test_a_finished_download_reports_the_library_file_it_became(
        self, client, db_session, monkeypatch
    ):
        from miru.catalog.models import CatalogWork
        from miru.streaming import partial as mod

        db_session.add(
            CatalogWork(
                kind="movie", normalised_title="spider man", display_title="Spider-Man",
                download_job_id="abc", library_file_id=40,
            )
        )
        db_session.commit()

        class Fake:
            def playable_prefix(self, h):
                return {
                    "info_hash": "abc", "name": "x.mkv", "file": "x.mkv",
                    "size_bytes": 10, "playable_bytes": 10, "progress": 1.0,
                    "complete": True, "sequential": True,
                    "save_path": "", "content_path": "",
                }

        monkeypatch.setattr(mod, "downloader", lambda: Fake())
        monkeypatch.setattr(mod, "supports_streaming", lambda: True)
        monkeypatch.setattr(mod, "_local_path", lambda p: None)

        body = client.get("/api/stream/live/abc/status").json()
        assert body["complete"] is True
        assert body["library_file_id"] == 40

    def test_an_unfinished_download_names_no_library_file(
        self, client, db_session, monkeypatch
    ):
        # Sending the player to the library mid-download would hand it a file
        # the scanner has not indexed and the mover has not moved.
        from miru.streaming import partial as mod

        class Fake:
            def playable_prefix(self, h):
                return {
                    "info_hash": "abc", "name": "x.mkv", "file": "x.mkv",
                    "size_bytes": 10, "playable_bytes": 4, "progress": 0.4,
                    "complete": False, "sequential": True,
                    "save_path": "", "content_path": "",
                }

        monkeypatch.setattr(mod, "downloader", lambda: Fake())
        monkeypatch.setattr(mod, "supports_streaming", lambda: True)
        monkeypatch.setattr(mod, "_local_path", lambda p: None)
        assert client.get("/api/stream/live/abc/status").json()["library_file_id"] is None


class TestProgressIsAFactNotAFeeling:
    """The library remux of a 3.8 GB film took ~7 minutes and reported nothing.

    The watch page showed a bare spinner the whole time and it was reported as
    "remux is failing" — the correct reading of what the UI showed. The number
    that would have said otherwise was on disk the entire time: the size of the
    .part file ffmpeg is writing, against the size of the source.
    """

    def test_a_running_remux_reports_how_far_it_has_got(self, tmp_path):
        src = tmp_path / "show.mkv"
        src.write_bytes(b"x" * 1000)
        part = remux.cached_path(1, src).with_suffix(".part.mp4")
        part.write_bytes(b"y" * 400)
        assert remux.progress(1, src) == pytest.approx(0.4)

    def test_the_number_grows_as_ffmpeg_writes(self, tmp_path):
        src = tmp_path / "show.mkv"
        src.write_bytes(b"x" * 1000)
        part = remux.cached_path(1, src).with_suffix(".part.mp4")
        part.write_bytes(b"y" * 100)
        early = remux.progress(1, src)
        part.write_bytes(b"y" * 900)
        assert remux.progress(1, src) > early

    def test_no_part_file_means_no_number(self, tmp_path):
        # None, not 0.0 — "not started" and "just started" are different
        # answers, and the UI should not show a stuck 0%.
        src = tmp_path / "show.mkv"
        src.write_bytes(b"x" * 1000)
        assert remux.progress(1, src) is None

    def test_a_remux_larger_than_its_source_never_reports_past_one(self, tmp_path):
        # Stream-copy output can exceed the source (container overhead). A
        # progress bar past 100% reads as broken.
        src = tmp_path / "show.mkv"
        src.write_bytes(b"x" * 100)
        part = remux.cached_path(1, src).with_suffix(".part.mp4")
        part.write_bytes(b"y" * 150)
        assert remux.progress(1, src) == 1.0

    def test_an_empty_source_does_not_divide_by_zero(self, tmp_path):
        src = tmp_path / "empty.mkv"
        src.write_bytes(b"")
        assert remux.progress(1, src) is None


class TestTheLiveRemuxIsHandedOverNotRedone:
    """One film, three remuxes in the cache — measured, ~6 GB for one title.

    The live path keys its remux on the infohash; the library path keys on the
    MediaFile id. When the mover promoted a finished download, the completed
    live remux was orphaned in the cache and the first library play remuxed the
    same film again from scratch, for another seven minutes.

    shutil.move preserves size and mtime, so the live digest is recomputable
    from the library file's own stat — the handover is a rename.
    """

    def _live_and_library(self, tmp_path):
        from miru.streaming.partial import info_hash_key

        lib_file = tmp_path / "film.mkv"
        lib_file.write_bytes(b"x" * 5000)
        info_hash = "9d86667f49f42712909c2888d346b37a17c44191"
        # The digest the live path used: same size/mtime (move preserves both),
        # the infohash-derived id, the incoming path's name is irrelevant.
        live = remux.cached_path(info_hash_key(info_hash), lib_file)
        return info_hash, lib_file, live

    def test_a_completed_live_remux_is_renamed_to_the_library_key(self, tmp_path):
        info_hash, lib_file, live = self._live_and_library(tmp_path)
        live.write_bytes(b"remuxed")

        assert remux.adopt(info_hash, file_id=40, library_path=lib_file) is True
        assert not live.exists()
        assert remux.cached_path(40, lib_file).read_bytes() == b"remuxed"
        # And the library path now serves it without starting anything.
        assert remux.state(40, lib_file) == "ready"

    def test_a_still_running_live_remux_is_left_alone(self, tmp_path):
        # Renaming the file ffmpeg is writing would corrupt both copies. The
        # library path falls back to a fresh remux, as today.
        info_hash, lib_file, live = self._live_and_library(tmp_path)
        live.with_suffix(".part.mp4").write_bytes(b"half")

        assert remux.adopt(info_hash, file_id=40, library_path=lib_file) is False
        assert not remux.cached_path(40, lib_file).exists()

    def test_no_live_remux_is_a_quiet_no(self, tmp_path):
        info_hash, lib_file, _ = self._live_and_library(tmp_path)
        assert remux.adopt(info_hash, file_id=40, library_path=lib_file) is False

    def test_an_existing_library_remux_is_not_overwritten(self, tmp_path):
        # If the library already remuxed it (user played before promotion
        # linked), the older live copy must not clobber the newer one.
        info_hash, lib_file, live = self._live_and_library(tmp_path)
        live.write_bytes(b"old live copy")
        remux.cached_path(40, lib_file).write_bytes(b"library copy")

        assert remux.adopt(info_hash, file_id=40, library_path=lib_file) is False
        assert remux.cached_path(40, lib_file).read_bytes() == b"library copy"


class TestPromotionActuallyAdopts:
    def test_link_by_filename_reaches_adopt_with_the_right_arguments(
        self, db_session, monkeypatch, tmp_path
    ):
        """The wiring, not the mechanism.

        adopt() sits inside a try/except in the promotion path, so a missing
        import or a wrong argument fails SILENTLY forever and the double remux
        quietly returns. This drives the real function end to end — a NameError
        in there is a test failure here, not a log line nobody reads.
        """
        from miru.catalog import router as mod

        lib_file = tmp_path / "Film.2026.mkv"
        lib_file.write_bytes(b"x" * 100)
        db_session.add(
            MediaFile(id=91, path=str(lib_file), title="Film", size_bytes=100, mtime=0.0)
        )
        w = CatalogWork(
            kind="movie", normalised_title="film", display_title="Film",
            download_job_id="9d86667f49f42712909c2888d346b37a17c44191",
        )
        db_session.add(w)
        db_session.commit()

        class Fake:
            def playable_prefix(self, h):
                return {"file": lib_file.name, "name": lib_file.name}

        monkeypatch.setattr(mod, "downloader", lambda: Fake())

        adopted = []
        from miru.streaming import remux

        monkeypatch.setattr(
            remux, "adopt", lambda ih, fid, p: adopted.append((ih, fid, p)) or True
        )
        mod._link_by_filename(db_session, w)
        assert adopted == [(w.download_job_id, 91, lib_file)]
