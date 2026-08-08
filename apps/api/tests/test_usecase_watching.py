"""Use case: watching something.

Covers what the browser actually does — Range requests while seeking, the HLS
handoff for files that need an encoder, and what the UI is told when the PC that
does the encoding is asleep.
"""

import pytest

from tests.conftest import make_file


@pytest.fixture
def media(tmp_path):
    f = tmp_path / "episode.mp4"
    f.write_bytes(bytes(range(256)) * 40)  # 10240 bytes
    return f


class TestSeeking:
    """Without correct Range handling every seek restarts the download, which is
    the whole difference between a media server and a download link."""

    def test_a_full_request_advertises_range_support(self, client, db_session, media):
        f = make_file(db_session, path=str(media))
        r = client.get(f"/api/stream/{f.id}")
        assert r.status_code == 200
        assert r.headers["accept-ranges"] == "bytes"
        assert r.headers["content-type"] == "video/mp4"

    def test_seeking_returns_exactly_the_requested_bytes(self, client, db_session, media):
        f = make_file(db_session, path=str(media))
        r = client.get(f"/api/stream/{f.id}", headers={"Range": "bytes=1000-1099"})
        assert r.status_code == 206
        assert r.headers["content-range"] == "bytes 1000-1099/10240"
        assert len(r.content) == 100

    def test_seeking_to_the_tail_works(self, client, db_session, media):
        # How a player fetches the moov atom of a badly-muxed MP4.
        f = make_file(db_session, path=str(media))
        r = client.get(f"/api/stream/{f.id}", headers={"Range": "bytes=-64"})
        assert r.status_code == 206 and len(r.content) == 64

    def test_an_impossible_range_is_refused_not_truncated(self, client, db_session, media):
        f = make_file(db_session, path=str(media))
        assert client.get(f"/api/stream/{f.id}",
                          headers={"Range": "bytes=99999-"}).status_code == 416

    def test_a_file_deleted_off_disk_reports_gone_not_crash(self, client, db_session, tmp_path):
        f = make_file(db_session, path=str(tmp_path / "vanished.mp4"))
        assert client.get(f"/api/stream/{f.id}").status_code == 410

    def test_unknown_file_is_a_404(self, client):
        assert client.get("/api/stream/9999").status_code == 404


class TestTranscodedPlayback:
    def test_a_direct_file_is_refused_by_the_hls_endpoint(self, client, db_session, worker_online):
        # Sending a playable file through the transcoder would burn GPU for
        # nothing, so this is a 409 rather than a silent redirect.
        f = make_file(db_session, playback_strategy="direct")
        r = client.get(f"/api/stream/{f.id}/index.m3u8")
        assert r.status_code == 409

    def test_a_transcode_file_hands_off_to_the_worker(self, client, db_session, worker_online):
        f = make_file(db_session, video_codec="hevc", playback_strategy="transcode_full")
        r = client.get(f"/api/stream/{f.id}/index.m3u8", follow_redirects=False)
        assert r.status_code == 302
        assert "pc:8010" in r.headers["location"]

    def test_the_worker_is_told_where_to_fetch_the_source(self, client, db_session, worker_online):
        # If this were localhost the PC would fetch itself and every transcode
        # would fail while both machines looked healthy.
        f = make_file(db_session, video_codec="hevc", playback_strategy="transcode_full")
        loc = client.get(f"/api/stream/{f.id}/index.m3u8",
                         follow_redirects=False).headers["location"]
        assert "laptop%3A8000" in loc or "laptop:8000" in loc
        assert "localhost" not in loc

    def test_the_ladder_is_capped_by_the_source_height(self, client, db_session, worker_online):
        f = make_file(db_session, height=720, video_codec="hevc",
                      playback_strategy="transcode_full")
        loc = client.get(f"/api/stream/{f.id}/index.m3u8",
                         follow_redirects=False).headers["location"]
        assert "height=720" in loc

    def test_audio_only_transcodes_tell_the_worker_to_copy_the_video(
        self, client, db_session, worker_online
    ):
        f = make_file(db_session, audio_codec="ac3", audio_channels=6,
                      playback_strategy="transcode_audio")
        loc = client.get(f"/api/stream/{f.id}/index.m3u8",
                         follow_redirects=False).headers["location"]
        assert "copy_video=true" in loc


class TestWhenThePcIsAsleep:
    """Only files needing an encoder depend on the PC. Everything else must keep
    working, and the UI must say which is which."""

    def test_direct_and_remux_stay_available(self, client, db_session):
        make_file(db_session, path="/a.mp4", title="a", playback_strategy="direct")
        make_file(db_session, path="/b.mkv", title="b", playback_strategy="remux")

        for f in client.get("/api/library").json():
            assert f["availability"] == "available"
            assert f["availability_note"] is None

    def test_a_sleeping_pc_is_reported_as_offline(self, client, db_session, worker_offline):
        make_file(db_session, video_codec="hevc", playback_strategy="transcode_full")
        f = client.get("/api/library").json()[0]
        assert f["availability"] == "unavailable"
        assert "offline" in f["availability_note"].lower()
        assert f["hls_url"] is None

    def test_never_configuring_a_worker_says_so_instead(self, client, db_session):
        # A different problem from a sleeping PC, and told apart on purpose:
        # one means turn the machine on, the other means you never set it up.
        make_file(db_session, video_codec="hevc", playback_strategy="transcode_full")
        f = client.get("/api/library").json()[0]
        assert f["availability"] == "unavailable"
        assert "configured" in f["availability_note"].lower()

    def test_the_hls_endpoint_refuses_rather_than_hanging(self, client, db_session, worker_offline):
        f = make_file(db_session, video_codec="hevc", playback_strategy="transcode_full")
        assert client.get(f"/api/stream/{f.id}/index.m3u8").status_code == 503

    def test_availability_recovers_when_the_pc_returns(self, client, db_session, worker_online):
        # Availability is derived per request precisely so this needs no rescan.
        f = make_file(db_session, video_codec="hevc", playback_strategy="transcode_full")
        body = client.get(f"/api/files/{f.id}").json()
        assert body["availability"] == "gpu-ready"
        assert body["hls_url"] and "pc:8010" in body["hls_url"]
