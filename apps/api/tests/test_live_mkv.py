"""Use case: pressing Watch Now on an MKV that is still downloading.

The live stream served the growing file exactly as it sits on disk, and told
the browser the truth about it:

    media_type = "video/mp4" if suffix in {".mp4", ".m4v"} else "video/x-matroska"

No browser plays Matroska. So every MKV download — which is most anime — was
unwatchable for the entire time it was downloading, which is the whole promise
of the button. The label being honest only made the failure silent: the player
was handed a container it refuses and had nothing to say about it.

The library already remuxes MKV to MP4 on this machine. It cannot be pointed at
a growing file as-is, because `+faststart` needs the whole thing to write the
index at the front. A fragmented MP4 needs no tail.
"""

from pathlib import Path

import pytest

from miru.streaming import remux


class TestAGrowingFileIsRemuxedWithoutItsTail:
    def test_a_fragmented_remux_does_not_ask_for_faststart(self, tmp_path):
        # +faststart moves the index to the front once the file is complete.
        # On a prefix there is no "complete", and ffmpeg would rewrite from a
        # tail that does not exist.
        cmd = remux.command(Path("in.mkv"), Path("out.mp4"), fragmented=True)
        assert "+faststart" not in " ".join(cmd)

    def test_a_fragmented_remux_writes_an_index_the_player_can_start_from(self, tmp_path):
        # empty_moov + frag_keyframe is what makes byte zero playable.
        flags = " ".join(remux.command(Path("in.mkv"), Path("out.mp4"), fragmented=True))
        assert "empty_moov" in flags and "frag_keyframe" in flags

    def test_a_complete_file_still_gets_faststart(self, tmp_path):
        # The library path is unchanged: a whole file remuxed once, seekable,
        # and smaller than a fragmented one.
        assert "+faststart" in " ".join(remux.command(Path("in.mkv"), Path("out.mp4")))


class TestTheCacheKnowsHowMuchWasThere:
    def test_a_longer_prefix_is_not_served_the_shorter_remux(self, tmp_path):
        """The failure this key exists to prevent.

        A growing file keeps its name and its inode. Keyed only on those, the
        remux of the first 200 MB would be served for the rest of the download
        and the video would stop at that point forever.
        """
        src = tmp_path / "a.mkv"
        src.write_bytes(b"x" * 100)
        short = remux.cached_path(1, src, prefix_bytes=100)
        longer = remux.cached_path(1, src, prefix_bytes=900)
        assert short != longer

    def test_the_same_prefix_hits_the_same_file(self, tmp_path):
        src = tmp_path / "a.mkv"
        src.write_bytes(b"x" * 100)
        assert remux.cached_path(1, src, prefix_bytes=100) == remux.cached_path(
            1, src, prefix_bytes=100
        )

    def test_a_complete_file_keys_the_same_way_it_always_did(self, tmp_path):
        # No prefix means the library path, and its cache entries must not all
        # be invalidated by this change.
        src = tmp_path / "a.mkv"
        src.write_bytes(b"x" * 100)
        assert remux.cached_path(1, src).suffix == ".mp4"


class TestWhatTheBrowserIsTold:
    @pytest.mark.parametrize("name", ["show.mkv", "show.MKV", "show.avi"])
    def test_a_container_no_browser_plays_is_never_offered_raw(self, name):
        from miru.streaming.partial import needs_remux

        assert needs_remux(Path(name)) is True

    @pytest.mark.parametrize("name", ["show.mp4", "show.m4v", "show.webm"])
    def test_a_container_the_browser_plays_is_served_as_it_is(self, name):
        from miru.streaming.partial import needs_remux

        assert needs_remux(Path(name)) is False


class TestTheLiveEndpointServesSomethingPlayable:
    def _wire(self, monkeypatch, tmp_path, name, complete=False, ready=None):
        from miru.streaming import partial as mod

        src = tmp_path / name
        src.write_bytes(b"\0" * 4096)
        prefix = {
            "info_hash": "abc", "name": name, "file": name, "size_bytes": 4096,
            "playable_bytes": ready if ready is not None else 4096,
            "progress": 1.0 if complete else 0.4, "complete": complete,
            "sequential": True, "save_path": "", "content_path": "",
        }

        class Fake:
            def playable_prefix(self, h):
                return prefix

        monkeypatch.setattr(mod, "downloader", lambda: Fake())
        monkeypatch.setattr(mod, "supports_streaming", lambda: True)
        monkeypatch.setattr(mod, "_local_path", lambda p: src)
        return src

    def test_an_mkv_prefix_is_not_handed_over_as_matroska(
        self, client, monkeypatch, tmp_path
    ):
        # The reported failure. Whatever the endpoint decides to do about it,
        # what it must never do is hand a browser a container it refuses.
        from miru.streaming import remux

        self._wire(monkeypatch, tmp_path, "show.mkv")
        monkeypatch.setattr(remux, "ensure", lambda *a, **k: "working")
        res = client.get("/api/stream/live/abc", headers={"Range": "bytes=0-99"})
        assert res.headers.get("content-type", "") != "video/x-matroska"

    def test_an_mp4_prefix_is_served_directly_with_no_remux(
        self, client, monkeypatch, tmp_path
    ):
        # The common case must not pay for the uncommon one: an MP4 is already
        # playable and remuxing it would delay every download that needs nothing.
        from miru.streaming import remux

        asked = []
        self._wire(monkeypatch, tmp_path, "show.mp4")
        monkeypatch.setattr(remux, "ensure", lambda *a, **k: asked.append(a) or "ready")
        res = client.get("/api/stream/live/abc", headers={"Range": "bytes=0-99"})
        assert res.status_code == 206
        assert res.headers["content-type"] == "video/mp4"
        assert asked == []

    def test_while_the_remux_runs_the_player_is_told_to_wait_not_given_rubbish(
        self, client, monkeypatch, tmp_path
    ):
        # 503 with Retry-After, because the answer is "not yet" rather than
        # "never" — and the buffering overlay already knows how to sit through
        # a wait. Zeros or a wrong container would look like a corrupt file.
        from miru.streaming import remux

        self._wire(monkeypatch, tmp_path, "show.mkv")
        monkeypatch.setattr(remux, "ensure", lambda *a, **k: "working")
        res = client.get("/api/stream/live/abc", headers={"Range": "bytes=0-99"})
        assert res.status_code == 503
        assert "retry-after" in {k.lower() for k in res.headers}

    def test_a_failed_remux_says_why(self, client, monkeypatch, tmp_path):
        from miru.streaming import remux

        self._wire(monkeypatch, tmp_path, "show.mkv")
        monkeypatch.setattr(remux, "ensure", lambda *a, **k: "failed")
        monkeypatch.setattr(remux, "error", lambda *a, **k: "ffmpeg exited 1")
        res = client.get("/api/stream/live/abc")
        assert res.status_code == 502
        assert "ffmpeg" in res.json()["detail"]


class TestOnlyTheCompletedPrefixIsRead:
    def test_ffmpeg_is_fed_the_prefix_rather_than_pointed_at_the_file(self, tmp_path):
        """A sequential torrent file is not short — it is full of holes.

        Watch Now asks for `firstLastPiecePrio`, so the LAST piece lands almost
        immediately and the file on disk is already its final size with a hole
        in the middle. Pointing ffmpeg at it means reading zeros, and zeros
        decode as corruption rather than as an error anyone can see — which is
        the exact failure the byte ceiling exists to prevent, reintroduced one
        layer down.
        """
        cmd = remux.command(Path("in.mkv"), Path("out.mp4"), fragmented=True, limit_bytes=100)
        assert "pipe:0" in cmd, cmd

    def test_a_complete_file_is_still_read_from_disk(self, tmp_path):
        # Seeking during the remux of a whole file is free and worth having.
        cmd = remux.command(Path("in.mkv"), Path("out.mp4"))
        assert "pipe:0" not in cmd
        assert "in.mkv" in cmd

    def test_exactly_the_prefix_is_written_and_no_more(self, tmp_path):
        src = tmp_path / "a.mkv"
        src.write_bytes(b"A" * 500 + b"\0" * 500)
        got = bytearray()
        for chunk in remux.read_prefix(src, 500):
            got += chunk
        assert bytes(got) == b"A" * 500

    def test_a_prefix_longer_than_the_file_stops_at_the_file(self, tmp_path):
        # The piece report and the filesystem are two machines' opinions.
        src = tmp_path / "a.mkv"
        src.write_bytes(b"A" * 10)
        assert b"".join(remux.read_prefix(src, 9999)) == b"A" * 10
