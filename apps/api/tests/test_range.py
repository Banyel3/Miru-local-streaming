"""Range support is the one thing M1 cannot get wrong: without it every seek
restarts the download. Starlette implements it, this holds it to that."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from miru.core.db import get_db
from miru.library.models import MediaFile
from miru.streaming.router import router

BODY = bytes(range(256)) * 8  # 2048 bytes


@pytest.fixture
def client(tmp_path):
    media = tmp_path / "sample.mp4"
    media.write_bytes(BODY)

    record = MediaFile(id=1, path=str(media), title="sample")
    missing = MediaFile(id=2, path=str(tmp_path / "gone.mp4"), title="gone")

    class FakeSession:
        def get(self, _model, pk):
            return {1: record, 2: missing}.get(pk)

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: FakeSession()
    return TestClient(app)


def test_full_request_advertises_range_support(client):
    r = client.get("/api/stream/1")
    assert r.status_code == 200
    assert r.headers["accept-ranges"] == "bytes"
    assert r.headers["content-type"] == "video/mp4"
    assert r.content == BODY


def test_range_request_returns_206_with_exact_slice(client):
    r = client.get("/api/stream/1", headers={"Range": "bytes=100-199"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 100-199/{len(BODY)}"
    assert r.headers["content-length"] == "100"
    assert r.content == BODY[100:200]


def test_open_ended_range_runs_to_end_of_file(client):
    r = client.get("/api/stream/1", headers={"Range": "bytes=2000-"})
    assert r.status_code == 206
    assert r.headers["content-range"] == f"bytes 2000-{len(BODY) - 1}/{len(BODY)}"
    assert r.content == BODY[2000:]


def test_suffix_range_reads_from_the_tail(client):
    # How a player fetches the moov atom of a badly-muxed MP4.
    r = client.get("/api/stream/1", headers={"Range": "bytes=-48"})
    assert r.status_code == 206
    assert r.content == BODY[-48:]


def test_unsatisfiable_range_is_rejected(client):
    r = client.get("/api/stream/1", headers={"Range": "bytes=99999-"})
    assert r.status_code == 416


def test_missing_file_is_410_not_500(client):
    assert client.get("/api/stream/2").status_code == 410


def test_unknown_id_is_404(client):
    assert client.get("/api/stream/99").status_code == 404


class TestTheStatusRouteSaysWhatIsHappening:
    """A 3.8 GB remux ran seven minutes behind a bare spinner.

    The 425 the stream answers lands inside the <video> element's own request,
    where no page code can read it — so the watch page needs a route it CAN
    read: state plus a percent, cheap enough to poll every 1.5s.
    """

    @pytest.fixture
    def remuxing(self, tmp_path, monkeypatch):
        from miru.streaming import remux

        media = tmp_path / "show.mkv"
        media.write_bytes(b"x" * 1000)
        record = MediaFile(id=7, path=str(media), title="show", playback_strategy="remux")
        direct = tmp_path / "plain.mp4"
        direct.write_bytes(BODY)
        plain = MediaFile(id=8, path=str(direct), title="plain", playback_strategy="direct")

        class FakeSession:
            def get(self, _model, pk):
                return {7: record, 8: plain}.get(pk)

        app = FastAPI()
        app.include_router(router)
        app.dependency_overrides[get_db] = lambda: FakeSession()
        monkeypatch.setattr(remux.settings, "remux_cache_path", str(tmp_path / "cache"))
        monkeypatch.setattr(remux, "_running", {})
        monkeypatch.setattr(remux, "_failed", {})
        return TestClient(app), media, remux

    def test_a_working_remux_reports_its_percent(self, remuxing, monkeypatch):
        client, media, remux = remuxing
        part = remux.cached_path(7, media).with_suffix(".part.mp4")
        part.parent.mkdir(parents=True, exist_ok=True)
        part.write_bytes(b"y" * 430)
        monkeypatch.setattr(remux, "state", lambda *a, **k: "working")
        body = client.get("/api/stream/7/status").json()
        assert body["state"] == "working"
        assert body["percent"] == 43

    def test_a_finished_remux_is_ready(self, remuxing, monkeypatch):
        client, media, remux = remuxing
        monkeypatch.setattr(remux, "state", lambda *a, **k: "ready")
        assert client.get("/api/stream/7/status").json()["state"] == "ready"

    def test_a_failed_remux_says_why(self, remuxing, monkeypatch):
        client, media, remux = remuxing
        monkeypatch.setattr(remux, "state", lambda *a, **k: "failed")
        monkeypatch.setattr(remux, "error", lambda *a, **k: "ffmpeg exited 1")
        body = client.get("/api/stream/7/status").json()
        assert body["state"] == "failed"
        assert "ffmpeg" in body["error"]

    def test_a_direct_file_is_ready_without_touching_the_remux(self, remuxing):
        # Most of the library never needs ffmpeg; the page must not wait on a
        # status that will never change.
        client, _, _ = remuxing
        assert client.get("/api/stream/8/status").json()["state"] == "ready"

    def test_the_425_tells_the_client_when_to_come_back(self, remuxing, monkeypatch):
        # The live path's 503 carries Retry-After and lib/live.ts reads it as
        # "waiting"; the library 425 carried nothing.
        client, _, remux = remuxing
        monkeypatch.setattr(remux, "state", lambda *a, **k: "working")
        monkeypatch.setattr(remux, "ensure", lambda *a, **k: "working")
        r = client.get("/api/stream/7")
        assert r.status_code == 425
        assert "retry-after" in {k.lower() for k in r.headers}
