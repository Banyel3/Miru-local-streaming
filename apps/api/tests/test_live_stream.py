"""Use case: watching something that is still downloading.

The correctness argument is the ceiling. Percent-complete says nothing about
*which* percent, and serving a hole returns zeros that decode as corruption
rather than as an error anyone can see, so every read is capped at the
contiguous downloaded prefix.
"""

import pytest

from miru.streaming import partial as live


@pytest.fixture
def growing(tmp_path, monkeypatch):
    """A file on disk with only its first half written."""
    from miru.core.config import settings

    inc = tmp_path / "incoming"
    inc.mkdir()
    f = inc / "Show.S01E01.1080p.mkv"
    f.write_bytes(bytes(range(256)) * 40)          # 10240 bytes on disk
    monkeypatch.setattr(settings, "incoming_path", str(inc))
    monkeypatch.setattr(settings, "downloader", "qbittorrent")
    return f


def fake_prefix(**kw):
    base = {
        "info_hash": "abc",
        "name": "Show.S01E01.1080p.mkv",
        "file": "Show.S01E01.1080p.mkv",
        "size_bytes": 20480,          # half of it has not arrived
        "save_path": "/downloads",
        "content_path": "",
        "progress": 0.5,
        "playable_bytes": 8192,
        "sequential": True,
        "complete": False,
    }
    return {**base, **kw}


@pytest.fixture
def dl(monkeypatch):
    class Fake:
        prefix = fake_prefix()

        def playable_prefix(self, h):
            return self.prefix

    fake = Fake()
    monkeypatch.setattr(live, "downloader", lambda: fake)
    monkeypatch.setattr(live, "supports_streaming", lambda: True)
    return fake


class TestTheCeiling:
    def test_a_read_inside_the_prefix_is_served(self, client, growing, dl):
        r = client.get("/api/stream/live/abc", headers={"Range": "bytes=0-1023"})
        assert r.status_code == 206
        assert len(r.content) == 1024
        assert r.headers["content-range"] == "bytes 0-1023/20480"

    def test_seeking_past_what_has_arrived_is_refused_not_padded(self, client, growing, dl):
        # Serving zeros here would decode as corruption, which looks like a
        # broken file rather than a download still in progress.
        r = client.get("/api/stream/live/abc", headers={"Range": "bytes=9000-9999"})
        assert r.status_code == 416

    def test_an_open_ended_range_stops_at_the_prefix(self, client, growing, dl):
        r = client.get("/api/stream/live/abc", headers={"Range": "bytes=0-"})
        assert r.status_code == 206
        assert r.headers["content-range"] == "bytes 0-8191/20480"

    def test_the_prefix_never_exceeds_what_is_actually_on_disk(self, client, growing, dl):
        # The piece report and the NFS write are two machines' opinions. The
        # smaller one is the safe one.
        dl.prefix = fake_prefix(playable_bytes=999_999)
        r = client.get("/api/stream/live/abc", headers={"Range": "bytes=0-"})
        assert r.headers["content-range"] == "bytes 0-10239/20480"

    def test_a_finished_download_serves_the_whole_file(self, client, growing, dl):
        dl.prefix = fake_prefix(complete=True, progress=1.0, size_bytes=10240)
        r = client.get("/api/stream/live/abc", headers={"Range": "bytes=0-"})
        assert r.headers["content-range"] == "bytes 0-10239/10240"

    def test_nothing_playable_yet_is_a_416_rather_than_an_empty_200(self, client, growing, dl):
        dl.prefix = fake_prefix(playable_bytes=0)
        assert client.get("/api/stream/live/abc").status_code == 416

    def test_the_response_says_how_far_it_can_be_scrubbed(self, client, growing, dl):
        r = client.get("/api/stream/live/abc", headers={"Range": "bytes=0-99"})
        assert r.headers["x-miru-playable-bytes"] == "8192"

    def test_the_prefix_is_never_cached(self, client, growing, dl):
        # The ceiling moves as pieces land; a cached 206 would freeze it.
        r = client.get("/api/stream/live/abc", headers={"Range": "bytes=0-99"})
        assert r.headers["cache-control"] == "no-store"


class TestReadiness:
    def test_a_trickle_is_not_yet_watchable(self, client, growing, dl):
        # Playback that starts and immediately freezes is worse than playback
        # that starts twenty seconds later.
        dl.prefix = fake_prefix(playable_bytes=1024)
        assert client.get("/api/stream/live/abc/status").json()["watchable"] is False

    def test_enough_of_the_front_is_watchable(self, client, growing, dl):
        dl.prefix = fake_prefix(playable_bytes=live.MIN_PLAYABLE_BYTES + 1)
        assert client.get("/api/stream/live/abc/status").json()["watchable"] is True

    def test_a_finished_file_is_watchable_however_small(self, client, growing, dl):
        dl.prefix = fake_prefix(complete=True, playable_bytes=1024)
        assert client.get("/api/stream/live/abc/status").json()["watchable"] is True

    def test_a_file_not_on_disk_yet_is_reported_rather_than_500ing(self, client, dl, monkeypatch):
        from miru.core.config import settings

        monkeypatch.setattr(settings, "incoming_path", "/nonexistent")
        b = client.get("/api/stream/live/abc/status").json()
        assert b["found_on_disk"] is False and b["watchable"] is False


class TestSafety:
    def test_a_filename_cannot_escape_the_incoming_directory(self, client, growing, dl):
        # `file` comes from another service, so it is a name, never a path.
        dl.prefix = fake_prefix(file="../../etc/passwd")
        assert client.get("/api/stream/live/abc").status_code == 404

    def test_aria2_refuses_rather_than_serving_a_meaningless_prefix(
        self, client, growing, dl, monkeypatch
    ):
        # aria2 has no sequential download for BitTorrent, so "the first N bytes
        # are present" is not a claim that can be made about its files.
        monkeypatch.setattr(live, "supports_streaming", lambda: False)
        assert client.get("/api/stream/live/abc").status_code == 409
        assert client.get("/api/stream/live/abc/status").status_code == 409
