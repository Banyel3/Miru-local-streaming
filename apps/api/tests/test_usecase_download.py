"""Use case: finding something and downloading it.

Search goes through Prowlarr, downloads through aria2. Neither is contacted
here — both are faked at the HTTP seam so these run in CI with no PC, no
indexers and no network.
"""

import json
from unittest.mock import patch

import pytest

from miru.acquisition.prowlarr import AcquisitionError, ProwlarrAria2Provider

NYAA_HIT = {
    "guid": "magnet:?xt=urn:btih:abc123&dn=Bleach",
    "magnetUrl": None,
    "downloadUrl": None,
    "title": "[ToonsHub] BLEACH Thousand-Year Blood War S01E42 1080p",
    "indexer": "Nyaa.si",
    "size": 1_181_116_006,
    "seeders": 1470,
    "leechers": 12,
    "age": 2,
    "categories": [{"id": 5070, "name": "TV/Anime"}],
}

LOW_SEEDED = {**NYAA_HIT, "guid": "magnet:?xt=urn:btih:def456", "seeders": 3,
              "title": "[Nobody] BLEACH S01E42"}

TORRENT_FILE_HIT = {
    "guid": "https://tracker/details/1",
    "magnetUrl": None,
    "downloadUrl": "https://tracker/download/1.torrent",
    "title": "Some.Film.2024.1080p",
    "indexer": "The Pirate Bay",
    "size": 5_321_218_376,
    "seeders": 40,
    "leechers": 3,
    "age": 10,
    "categories": [],
    "imdbId": "tt3728733",
}

UNGRABBABLE = {**NYAA_HIT, "guid": "https://tracker/details/9", "downloadUrl": None,
               "magnetUrl": None, "title": "No way to fetch this"}


@pytest.fixture
def provider(monkeypatch):
    from miru.core.config import settings
    monkeypatch.setattr(settings, "prowlarr_url", "http://pc:9696")
    monkeypatch.setattr(settings, "prowlarr_api_key", "k")
    monkeypatch.setattr(settings, "aria2_url", "http://pc:6800")
    monkeypatch.setattr(settings, "aria2_secret", "s")
    return ProwlarrAria2Provider()


class TestSearching:
    def test_results_are_ranked_by_seeders(self, provider):
        # Prowlarr returns indexer order. Two hundred rows for one episode is
        # not a choice a person can make; seeders is the field that predicts
        # whether the download finishes.
        with patch("miru.acquisition.prowlarr._get_json", return_value=[LOW_SEEDED, NYAA_HIT]):
            got = provider.search("bleach")
        assert [r.seeders for r in got] == [1470, 3]

    def test_a_nyaa_magnet_is_read_out_of_guid(self, provider):
        with patch("miru.acquisition.prowlarr._get_json", return_value=[NYAA_HIT]):
            r = provider.search("bleach")[0]
        assert r.magnet == NYAA_HIT["guid"]
        assert r.id == NYAA_HIT["guid"]   # the magnet IS the id, so no server-side cache
        assert r.indexer == "Nyaa.si"

    def test_a_torrent_url_indexer_uses_downloadurl(self, provider):
        with patch("miru.acquisition.prowlarr._get_json", return_value=[TORRENT_FILE_HIT]):
            r = provider.search("film")[0]
        assert r.magnet is None and r.id == TORRENT_FILE_HIT["downloadUrl"]

    def test_results_that_cannot_be_grabbed_are_not_offered(self, provider):
        # Showing a result with no magnet and no torrent URL is a promise the
        # UI cannot keep.
        with patch("miru.acquisition.prowlarr._get_json", return_value=[UNGRABBABLE, NYAA_HIT]):
            got = provider.search("bleach")
        assert len(got) == 1 and got[0].id == NYAA_HIT["guid"]

    def test_metadata_ids_are_carried_through_for_m2(self, provider):
        with patch("miru.acquisition.prowlarr._get_json", return_value=[TORRENT_FILE_HIT]):
            assert provider.search("film")[0].imdb_id == "tt3728733"

    def test_an_unconfigured_prowlarr_is_an_error_not_an_empty_list(self, monkeypatch):
        from miru.core.config import settings
        monkeypatch.setattr(settings, "prowlarr_url", "")
        with pytest.raises(AcquisitionError):
            ProwlarrAria2Provider().search("anything")


class TestSearchThroughTheApi:
    def test_a_query_with_no_matches_returns_an_empty_list(self, client, provider):
        with patch("miru.acquisition.prowlarr._get_json", return_value=[]):
            r = client.get("/api/acquisition/search?q=zzzznothing")
        assert r.status_code == 200 and r.json() == []

    def test_broken_indexers_surface_as_an_error_not_as_no_results(self, client, provider):
        # The search stack this replaced collapsed every failure into an empty
        # array, so 'all indexers are down' and 'no matches' looked identical
        # and it stayed broken unnoticed.
        with patch("miru.acquisition.prowlarr._get_json",
                   side_effect=AcquisitionError("indexer unreachable")):
            r = client.get("/api/acquisition/search?q=bleach")
        assert r.status_code == 502
        assert "unreachable" in r.json()["detail"]

    def test_a_one_character_query_is_rejected(self, client):
        assert client.get("/api/acquisition/search?q=a").status_code == 422


class TestDownloading:
    def test_submitting_a_magnet_returns_a_job_id(self, provider):
        with patch("miru.acquisition.prowlarr._rpc", return_value="gid123") as rpc:
            job = provider.submit("magnet:?xt=urn:btih:abc")
        assert job.id == "gid123"
        method, params = rpc.call_args[0]
        assert method == "aria2.addUri"
        # No directory is passed: aria2's own config points at the drop-box, so
        # Miru never names a path that lives on another machine.
        assert params == [["magnet:?xt=urn:btih:abc"]]

    def test_submitting_something_that_is_not_a_torrent_is_refused(self, provider):
        for bad in ("", "not-a-url", "ftp://host/file", "/etc/passwd"):
            with pytest.raises(AcquisitionError):
                provider.submit(bad)

    def test_progress_is_reported_while_downloading(self, provider):
        with patch("miru.acquisition.prowlarr._rpc", return_value={
            "gid": "g1", "status": "active",
            "totalLength": "1000", "completedLength": "250",
            "downloadSpeed": "50",
            "bittorrent": {"info": {"name": "Bleach S01E42"}},
        }):
            s = provider.status("g1")
        assert s.state == "downloading"
        assert s.progress == 0.25
        assert s.name == "Bleach S01E42"
        assert s.eta_seconds == 15          # (1000-250)/50

    def test_aria2_states_are_translated_not_leaked(self, provider):
        for aria_state, expected in [
            ("active", "downloading"), ("waiting", "queued"), ("paused", "queued"),
            ("complete", "done"), ("error", "failed"), ("removed", "cancelled"),
        ]:
            with patch("miru.acquisition.prowlarr._rpc",
                       return_value={"status": aria_state, "totalLength": "1",
                                     "completedLength": "1", "downloadSpeed": "0"}):
                assert provider.status("g").state == expected, aria_state

    def test_a_failed_download_carries_its_reason(self, provider):
        with patch("miru.acquisition.prowlarr._rpc", return_value={
            "status": "error", "totalLength": "0", "completedLength": "0",
            "downloadSpeed": "0", "errorMessage": "no peers",
        }):
            s = provider.status("g1")
        assert s.state == "failed" and s.error == "no peers"

    def test_zero_length_download_does_not_divide_by_zero(self, provider):
        with patch("miru.acquisition.prowlarr._rpc", return_value={
            "status": "waiting", "totalLength": "0", "completedLength": "0",
            "downloadSpeed": "0",
        }):
            s = provider.status("g1")
        assert s.progress == 0.0 and s.eta_seconds is None

    def test_cancelling_a_finished_download_falls_through_to_removing_its_result(self, provider):
        # aria2.remove only works on an active download; a completed one has to
        # be cleared from the result list instead.
        calls = []

        def fake(method, params):
            calls.append(method)
            if method == "aria2.remove":
                raise AcquisitionError("not active")
            return "ok"

        with patch("miru.acquisition.prowlarr._rpc", side_effect=fake):
            provider.cancel("g1")
        assert calls == ["aria2.remove", "aria2.removeDownloadResult"]


class TestAria2Protocol:
    def test_the_secret_is_sent_as_the_first_parameter(self, provider, monkeypatch):
        sent = {}

        class FakeResp:
            def read(self): return json.dumps({"result": "ok"}).encode()
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=None):
            sent["body"] = json.loads(req.data)
            return FakeResp()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        from miru.acquisition.prowlarr import _rpc
        _rpc("aria2.getVersion", [])

        # Without this, anything on the tailnet could queue downloads onto the disk.
        assert sent["body"]["params"][0] == "token:s"
