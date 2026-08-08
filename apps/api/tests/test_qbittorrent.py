"""Use case: downloading through qBittorrent.

qBittorrent is the default because it is the only one of the two backends that
can produce a watchable partial file. Its protocol has three quirks that will
break this if they are not handled, and each has a test here: cookie sessions
that expire, a state vocabulary far wider than ours, and a magic ETA meaning
"no idea".

Nothing here talks to a real qBittorrent — the HTTP seam is faked.
"""

import json

import pytest

from miru.acquisition.provider import AcquisitionError
from miru.acquisition.qbittorrent import QBittorrentProvider, info_hash_of

MAGNET = "magnet:?xt=urn:btih:1609FD225B0817D34CDDAF6E460D70F7E39D1B1D&dn=Show&tr=udp%3A%2F%2Fx"


@pytest.fixture
def qb(monkeypatch):
    from miru.core.config import settings

    monkeypatch.setattr(settings, "qbittorrent_url", "http://pc:8080")
    monkeypatch.setattr(settings, "qbittorrent_user", "admin")
    monkeypatch.setattr(settings, "qbittorrent_password", "secret")
    monkeypatch.setattr(settings, "qbittorrent_save_path", "")
    import miru.acquisition.qbittorrent as mod

    monkeypatch.setattr(mod, "_opener", None)
    return QBittorrentProvider()


@pytest.fixture
def calls(monkeypatch):
    """Capture every call the provider makes, and answer it."""
    seen = []
    replies: dict[str, object] = {}

    def fake_call(path, params=None, *, retry=True):
        seen.append((path, params or {}))
        r = replies.get(path, "Ok.")
        return json.dumps(r) if not isinstance(r, str) else r

    import miru.acquisition.qbittorrent as mod

    monkeypatch.setattr(mod, "_call", fake_call)
    monkeypatch.setattr(mod, "_json", lambda p, q=None: replies.get(p, []))
    return seen, replies


class TestIdentity:
    def test_the_job_id_is_the_infohash(self, qb, calls):
        # Not something qBittorrent invents: a restart on either side must not
        # orphan an in-flight download.
        job = qb.submit(MAGNET)
        assert job.id == "1609fd225b0817d34cddaf6e460d70f7e39d1b1d"

    def test_an_infohash_is_read_out_of_a_magnet_case_insensitively(self):
        assert info_hash_of(MAGNET) == "1609fd225b0817d34cddaf6e460d70f7e39d1b1d"
        assert info_hash_of("ABCdef123") == "abcdef123"

    def test_a_magnet_without_an_infohash_is_refused(self):
        with pytest.raises(AcquisitionError):
            info_hash_of("magnet:?dn=nothing-useful")

    def test_something_that_is_not_a_torrent_is_refused(self, qb, calls):
        for bad in ("", "not-a-url", "ftp://host/x", "/etc/passwd"):
            with pytest.raises(AcquisitionError):
                qb.submit(bad)

    def test_a_torrent_url_is_refused_rather_than_becoming_the_job_id(self, qb, calls):
        # qBittorrent will happily add a .torrent by URL, but it does not tell
        # us the infohash, and the infohash is what a job id has to be. The old
        # code used the URL itself, so every later status/pause/cancel looked up
        # a torrent qBittorrent has never heard of: the download ran invisibly
        # and the UI showed it as failed forever. An honest refusal at the seam
        # beats a job that can never be tracked.
        with pytest.raises(AcquisitionError) as exc:
            qb.submit("https://prowlarr.local/download/abc.torrent")
        assert "magnet" in str(exc.value).lower()

    def test_nothing_is_sent_to_qbittorrent_when_the_link_is_refused(self, qb, calls):
        # Refusing after adding it would leave a running download nothing can
        # reach — the same invisible-download failure, one step later.
        seen, _ = calls
        with pytest.raises(AcquisitionError):
            qb.submit("https://prowlarr.local/download/abc.torrent")
        assert seen == []


class TestSequentialIsTheWholePoint:
    def test_watch_now_asks_for_sequential_and_the_tail_first(self, qb, calls):
        seen, _ = calls
        qb.submit(MAGNET, sequential=True)
        _, params = seen[0]
        assert params["sequentialDownload"] == "true"
        # Without the tail a badly muxed MP4 cannot start at all, however much
        # of the front has landed.
        assert params["firstLastPiecePrio"] == "true"

    def test_a_plain_download_leaves_piece_order_alone(self, qb, calls):
        seen, _ = calls
        qb.submit(MAGNET, sequential=False)
        _, params = seen[0]
        assert params["sequentialDownload"] == "false"
        assert "firstLastPiecePrio" not in params

    def test_a_running_download_can_be_made_watchable(self, qb, calls):
        # The argument for one client rather than two: changing your mind costs
        # an API call instead of losing all progress.
        seen, _ = calls
        qb.make_sequential("ABC")
        paths = [p for p, _ in seen]
        assert paths == ["/torrents/toggleSequentialDownload", "/torrents/toggleFirstLastPiecePrio"]
        assert all(q["hashes"] == "abc" for _, q in seen)

    def test_miru_does_not_name_a_directory_on_another_machine(self, qb, calls):
        seen, _ = calls
        qb.submit(MAGNET)
        assert "savepath" not in seen[0][1]


class TestStatus:
    def _row(self, **kw):
        base = {
            "state": "downloading",
            "progress": 0.41,
            "size": 1_400_000_000,
            "completed": 574_000_000,
            "dlspeed": 8_200_000,
            "eta": 180,
            "name": "Show S01E01",
        }
        return [{**base, **kw}]

    def test_progress_speed_and_eta_come_through(self, qb, calls):
        _, replies = calls
        replies["/torrents/info"] = self._row()
        s = qb.status("abc")
        assert s.progress == 0.41
        assert s.speed_bps == 8_200_000
        assert s.eta_seconds == 180
        assert s.name == "Show S01E01"

    def test_the_magic_eta_meaning_no_idea_is_dropped(self, qb, calls):
        # qBittorrent sends 8640000 (100 days) when it cannot tell. Showing
        # "100 days left" is worse than showing nothing.
        _, replies = calls
        replies["/torrents/info"] = self._row(eta=8640000)
        assert qb.status("abc").eta_seconds is None

    @pytest.mark.parametrize(
        "qb_state,expected",
        [
            ("downloading", "downloading"),
            ("stalledDL", "downloading"),
            ("forcedDL", "downloading"),
            ("metaDL", "queued"),
            ("queuedDL", "queued"),
            # Paused is its own state, not "queued". Collapsing them meant a
            # paused download rendered as queued with a stale ETA, so the pause
            # button would have looked like it failed.
            ("pausedDL", "paused"),
            ("stoppedDL", "paused"),
            ("uploading", "done"),
            ("stalledUP", "done"),
            ("pausedUP", "done"),
            ("moving", "done"),
            ("error", "failed"),
            ("missingFiles", "failed"),
        ],
    )
    def test_qbittorrent_states_are_translated_not_leaked(self, qb, calls, qb_state, expected):
        # Its vocabulary is much wider than ours and leaks implementation detail
        # — three separate flavours of paused, two of stalled.
        _, replies = calls
        replies["/torrents/info"] = self._row(state=qb_state)
        assert qb.status("abc").state == expected, qb_state

    def test_a_torrent_it_has_forgotten_is_an_error_not_a_blank(self, qb, calls):
        _, replies = calls
        replies["/torrents/info"] = []
        with pytest.raises(AcquisitionError):
            qb.status("abc")


class TestCancelling:
    def test_cancelling_keeps_the_bytes(self, qb, calls):
        # A file the user may already be part-way through watching should not
        # vanish because they cancelled the download.
        seen, _ = calls
        qb.cancel("ABC")
        path, params = seen[0]
        assert path == "/torrents/delete"
        assert params["deleteFiles"] == "false"


class TestConfiguration:
    def test_an_unconfigured_backend_reports_itself_rather_than_failing_late(self, monkeypatch):
        from miru.core.config import settings

        monkeypatch.setattr(settings, "qbittorrent_url", "")
        assert QBittorrentProvider().configured() is False

    def test_the_default_downloader_is_the_one_that_can_stream(self, monkeypatch):
        from miru.acquisition.downloader import downloader, supports_streaming
        from miru.core.config import settings

        monkeypatch.setattr(settings, "downloader", "qbittorrent")
        assert type(downloader()).__name__ == "QBittorrentProvider"
        assert supports_streaming() is True

    def test_falling_back_to_aria2_is_one_setting_and_admits_it_cannot_stream(self, monkeypatch):
        from miru.acquisition.downloader import downloader, supports_streaming
        from miru.core.config import settings

        monkeypatch.setattr(settings, "downloader", "aria2")
        assert type(downloader()).__name__ == "ProwlarrAria2Provider"
        # aria2 1.37 has no sequential download for BitTorrent at all, so a
        # partial file is never watchable. The UI must not promise otherwise.
        assert supports_streaming() is False


class TestTheReadablePrefixOfAMultiFileTorrent:
    """Pieces are torrent-wide; a file is not.

    The prefix is a count of completed pieces from piece zero, which is the
    start of the *torrent*. In a multi-file torrent the video does not start
    there — every byte of every earlier file sits in front of it. Reporting the
    torrent prefix as the video's readable prefix over-promises by exactly that
    offset, and the player reads a hole: zeros, which decode as corruption
    rather than as an error anyone can see.
    """

    def _files(self, *sizes):
        return [
            {"index": i, "name": f"f{i}.bin", "size": s, "progress": 0}
            for i, s in enumerate(sizes)
        ]

    def _wire(self, calls, files, ready_pieces, piece_len, total=None):
        _, replies = calls
        replies["/torrents/info"] = [
            {
                "name": "Pack",
                "progress": 0.5,
                "save_path": "/incoming",
                "content_path": "/incoming/Pack",
                "seq_dl": True,
            }
        ]
        replies["/torrents/files"] = files
        replies["/torrents/pieceStates"] = [2] * ready_pieces + [0] * 40
        replies["/torrents/properties"] = {"piece_size": piece_len}

    def test_bytes_belonging_to_earlier_files_are_not_counted_as_the_videos(
        self, qb, calls
    ):
        # 30 MB of extras sit in front of a 100 MB video. 50 MB of pieces have
        # landed, so 20 MB of the video is actually readable.
        mb = 1024 * 1024
        self._wire(calls, self._files(30 * mb, 100 * mb), ready_pieces=50, piece_len=mb)
        assert qb.playable_prefix("abc")["playable_bytes"] == 20 * mb

    def test_nothing_is_readable_until_the_video_itself_starts(self, qb, calls):
        # The pieces that have landed are entirely inside the earlier file. The
        # honest answer is zero, not "10 MB" — the player would read a hole.
        mb = 1024 * 1024
        self._wire(calls, self._files(30 * mb, 100 * mb), ready_pieces=10, piece_len=mb)
        assert qb.playable_prefix("abc")["playable_bytes"] == 0

    def test_a_single_file_torrent_is_unchanged(self, qb, calls):
        mb = 1024 * 1024
        self._wire(calls, self._files(100 * mb), ready_pieces=40, piece_len=mb)
        assert qb.playable_prefix("abc")["playable_bytes"] == 40 * mb

    def test_the_prefix_never_exceeds_the_file(self, qb, calls):
        # Trailing files are downloaded too, so the piece count runs past the
        # end of the video. Content-Length is built from this.
        mb = 1024 * 1024
        self._wire(
            calls, self._files(10 * mb, 50 * mb, 10 * mb), ready_pieces=70, piece_len=mb
        )
        assert qb.playable_prefix("abc")["playable_bytes"] == 50 * mb

    def test_the_biggest_file_is_still_the_one_chosen(self, qb, calls):
        mb = 1024 * 1024
        self._wire(calls, self._files(2 * mb, 500 * mb), ready_pieces=200, piece_len=mb)
        p = qb.playable_prefix("abc")
        assert p["size_bytes"] == 500 * mb
        assert p["file"] == "f1.bin"


class TestAskingAboutEveryDownloadAtOnce:
    """The downloads poll ran one HTTP call per work, every few seconds.

    It also could only ever ask about downloads a work still points at, which
    is how a merge made a running download disappear from the screen entirely
    — no progress, no pause, no cancel, while it carried on downloading.
    """

    def _rows(self, *hashes):
        return [
            {
                "hash": h,
                "state": "downloading",
                "progress": 0.5,
                "size": 100,
                "completed": 50,
                "dlspeed": 10,
                "eta": 60,
                "name": f"Show {h}",
            }
            for h in hashes
        ]

    def test_one_call_answers_for_every_download(self, qb, calls):
        seen, replies = calls
        replies["/torrents/info"] = self._rows("aaa", "bbb", "ccc")
        assert len(qb.statuses()) == 3

    def test_it_asks_for_everything_rather_than_naming_hashes(self, qb, calls):
        # Naming them would reintroduce the other half of the bug: a download
        # no work points at could never come back.
        seen, replies = calls
        replies["/torrents/info"] = self._rows("aaa")
        qb.statuses()
        assert all("hashes" not in q for _, q in seen)

    def test_hashes_are_lowercased_so_a_lookup_matches(self, qb, calls):
        # qBittorrent returns them lowercase, but a job id came from a magnet
        # that may not have been. A case mismatch here silently reports every
        # download as forgotten.
        _, replies = calls
        replies["/torrents/info"] = self._rows("AAABBB")
        assert "aaabbb" in qb.statuses()

    def test_states_are_translated_the_same_way_as_a_single_lookup(self, qb, calls):
        _, replies = calls
        rows = self._rows("aaa")
        rows[0]["state"] = "pausedDL"
        replies["/torrents/info"] = rows
        assert qb.statuses()["aaa"].state == "paused"

    def test_nothing_downloading_is_not_an_error(self, qb, calls):
        _, replies = calls
        replies["/torrents/info"] = []
        assert qb.statuses() == {}
