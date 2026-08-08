"""Use case: the downloads screen, while things are downloading.

Two failures live here. The poll asked the downloader once per work — a request
per card every couple of seconds against a machine already saturating its disk.
And it could only ask about downloads a work still points at, so a download the
catalogue lost track of became invisible while it carried on running: no
progress, no pause, no cancel, and no way to reach it again.
"""

import pytest

from miru.acquisition.provider import DownloadStatus
from miru.catalog.models import CatalogWork


def _status(h, state="downloading", progress=0.5, name="Show"):
    return DownloadStatus(
        id=h,
        state=state,
        progress=progress,
        name=name,
        downloaded_bytes=50,
        total_bytes=100,
        speed_bps=10,
        eta_seconds=60,
        error=None,
    )


@pytest.fixture
def poll(monkeypatch):
    """Stand in for the downloader, counting how often it is asked."""
    from miru.catalog import router as mod

    calls = {"n": 0}
    table: dict[str, DownloadStatus] = {}

    class Fake:
        def statuses(self):
            calls["n"] += 1
            return dict(table)

        def status(self, job_id):
            calls["n"] += 1
            return table[job_id]

    monkeypatch.setattr(mod, "downloader", lambda: Fake())
    monkeypatch.setattr(mod, "pc_reachable", lambda: True)
    monkeypatch.setattr(mod, "supports_streaming", lambda: True)
    return calls, table


def _work(db, title, job):
    w = CatalogWork(
        kind="anime", normalised_title=title.lower(), display_title=title,
        download_job_id=job,
    )
    db.add(w)
    db.commit()
    return w


class TestOneRequestForTheWholeScreen:
    def test_eight_downloads_are_one_question(self, client, db_session, poll):
        calls, table = poll
        for i in range(8):
            table[f"h{i}"] = _status(f"h{i}")
            _work(db_session, f"Show {i}", f"h{i}")

        body = client.get("/api/catalog/downloads").json()
        assert len(body["downloads"]) == 8
        assert calls["n"] == 1, f"asked the downloader {calls['n']} times for 8 cards"


class TestADownloadCannotGoMissing:
    def test_a_download_no_card_points_at_is_still_shown(self, client, db_session, poll):
        """The merge bug, from the side the user sees.

        A work has one download_job_id. When enrichment merges two cards that
        each had a download in flight, one infohash is dropped — and with it
        every control for a download that is still running. qBittorrent still
        knows about it, so the screen can too.
        """
        _, table = poll
        table["kept"] = _status("kept")
        table["orphan"] = _status("orphan", name="The Merged One")
        _work(db_session, "Kept", "kept")

        got = client.get("/api/catalog/downloads").json()["downloads"]
        assert {d["job_id"] for d in got} == {"kept", "orphan"}

    def test_an_orphan_is_named_by_the_torrent_rather_than_left_blank(
        self, client, db_session, poll
    ):
        # There is no card to take a title from. The torrent's own name is what
        # the user recognises; "Unknown" with a pause button is not usable.
        _, table = poll
        table["orphan"] = _status("orphan", name="The Merged One")
        got = client.get("/api/catalog/downloads").json()["downloads"]
        assert got[0]["title"] == "The Merged One"

    def test_an_orphan_carries_no_work_id(self, client, db_session, poll):
        # The UI links a row to its card. There is no card, and inventing an id
        # would link the row to somebody else's.
        _, table = poll
        table["orphan"] = _status("orphan")
        assert client.get("/api/catalog/downloads").json()["downloads"][0]["work_id"] is None

    def test_a_card_whose_download_qbittorrent_has_forgotten_still_reports(
        self, client, db_session, poll
    ):
        # Deleted from qBittorrent directly. The card must say so rather than
        # silently vanishing, so the picker can be offered again.
        _, table = poll
        _work(db_session, "Gone", "vanished")
        got = client.get("/api/catalog/downloads").json()["downloads"]
        assert [(d["job_id"], d["state"]) for d in got] == [("vanished", "failed")]


class TestTheAsleepPcIsStillHandled:
    def test_an_unreachable_pc_is_reported_rather_than_waited_on(
        self, client, db_session, poll, monkeypatch
    ):
        from miru.catalog import router as mod

        calls, table = poll
        table["h"] = _status("h")
        _work(db_session, "Show", "h")
        monkeypatch.setattr(mod, "pc_reachable", lambda: False)

        body = client.get("/api/catalog/downloads").json()
        assert body["pc_reachable"] is False
        assert body["downloads"] == []
        assert calls["n"] == 0, "asked a machine known to be asleep"
