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
