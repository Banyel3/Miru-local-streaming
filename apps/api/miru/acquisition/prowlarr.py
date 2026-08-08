"""Search via Prowlarr, downloads via aria2 — the AcquisitionProvider in use.

Two services, one interface. Prowlarr owns indexer definitions so Miru never
parses a torrent site's HTML; aria2 owns downloading so Miru never implements
resume or queueing. Both run on the PC and are reached over the tailnet.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from miru.acquisition.provider import DownloadJob, DownloadStatus, SearchResult
from miru.core.config import settings

log = logging.getLogger(__name__)

TIMEOUT_S = 45.0


class AcquisitionError(RuntimeError):
    pass


def _get_json(url: str, headers: dict[str, str] | None = None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        raise AcquisitionError(f"{url.split('?')[0]} → HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise AcquisitionError(f"could not reach {url.split('?')[0]}: {exc}") from exc


def _rpc(method: str, params: list) -> dict:
    """aria2 JSON-RPC. The secret is always the first parameter, as a token."""
    if not settings.aria2_url:
        raise AcquisitionError("no aria2 configured")

    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": "miru",
            "method": method,
            "params": [f"token:{settings.aria2_secret}", *params],
        }
    ).encode()

    req = urllib.request.Request(
        f"{settings.aria2_url.rstrip('/')}/jsonrpc",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:
            body = json.loads(resp.read())
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise AcquisitionError(f"aria2 unreachable: {exc}") from exc

    if "error" in body:
        raise AcquisitionError(f"aria2: {body['error'].get('message', body['error'])}")
    return body["result"]


class ProwlarrAria2Provider:
    """Search through Prowlarr, download through aria2."""

    def configured(self) -> bool:
        return bool(settings.prowlarr_url and settings.prowlarr_api_key)

    def search(self, query: str, limit: int = 50) -> list[SearchResult]:
        if not self.configured():
            raise AcquisitionError("Prowlarr is not configured")

        qs = urllib.parse.urlencode({"query": query, "limit": limit, "type": "search"})
        raw = _get_json(
            f"{settings.prowlarr_url.rstrip('/')}/api/v1/search?{qs}",
            {"X-Api-Key": settings.prowlarr_api_key},
        )

        results = [self._to_result(r) for r in raw]
        # Prowlarr returns whatever each indexer gives, in indexer order. Seeders
        # is the one field that actually predicts whether a download will finish,
        # so rank by it rather than making the user scan two hundred rows.
        results.sort(key=lambda r: r.seeders, reverse=True)
        return [r for r in results if r.grabbable]

    @staticmethod
    def _to_result(r: dict) -> SearchResult:
        # Nyaa returns the magnet in `guid` and leaves downloadUrl null; other
        # indexers do the reverse. Take whichever is present.
        guid = r.get("guid") or ""
        magnet = r.get("magnetUrl") or (guid if guid.startswith("magnet:") else None)

        return SearchResult(
            # The magnet (or torrent URL) *is* the identifier: it is what aria2
            # needs, and it means submit() requires no server-side result cache
            # that would have to be kept in sync with a search the user may have
            # run minutes ago.
            id=magnet or r.get("downloadUrl") or guid,
            title=r.get("title") or r.get("fileName") or "(untitled)",
            indexer=r.get("indexer") or "unknown",
            size_bytes=int(r.get("size") or 0),
            seeders=int(r.get("seeders") or 0),
            leechers=int(r.get("leechers") or 0),
            age_days=int(r.get("age") or 0),
            magnet=magnet,
            download_url=r.get("downloadUrl"),
            categories=[c.get("name") for c in (r.get("categories") or []) if c.get("name")],
            imdb_id=str(r["imdbId"]) if r.get("imdbId") else None,
            tmdb_id=r.get("tmdbId") or None,
            tvdb_id=r.get("tvdbId") or None,
        )

    def submit(self, result_id: str) -> DownloadJob:
        """Hand a magnet or torrent URL to aria2.

        `dir` is not passed: aria2's own config points at the incoming directory,
        so Miru never names a filesystem path that lives on another machine.
        """
        if not result_id.startswith(("magnet:", "http://", "https://")):
            raise AcquisitionError("result id is not a magnet or torrent URL")

        gid = _rpc("aria2.addUri", [[result_id]])
        log.info("queued download %s", gid)
        return DownloadJob(id=gid, result_id=result_id)

    def status(self, job_id: str) -> DownloadStatus:
        r = _rpc(
            "aria2.tellStatus",
            [job_id, ["gid", "status", "totalLength", "completedLength",
                      "downloadSpeed", "errorMessage", "files", "bittorrent"]],
        )

        total = int(r.get("totalLength") or 0)
        done = int(r.get("completedLength") or 0)
        speed = int(r.get("downloadSpeed") or 0)

        # aria2's vocabulary is close to ours but not identical; map rather than
        # leak it into the UI.
        state = {
            "active": "downloading",
            "waiting": "queued",
            "paused": "queued",
            "complete": "done",
            "error": "failed",
            "removed": "cancelled",
        }.get(r.get("status", ""), "queued")

        name = (r.get("bittorrent", {}).get("info", {}) or {}).get("name")
        if not name and r.get("files"):
            name = (r["files"][0].get("path") or "").rsplit("/", 1)[-1] or None

        return DownloadStatus(
            id=job_id,
            state=state,
            progress=(done / total) if total else 0.0,
            name=name,
            downloaded_bytes=done,
            total_bytes=total,
            speed_bps=speed,
            eta_seconds=int((total - done) / speed) if speed and total > done else None,
            error=r.get("errorMessage") or None,
        )

    def cancel(self, job_id: str) -> None:
        # `remove` stops an active download; a finished or errored one is only
        # removable from the result list, so fall through rather than error.
        try:
            _rpc("aria2.remove", [job_id])
        except AcquisitionError:
            _rpc("aria2.removeDownloadResult", [job_id])


provider = ProwlarrAria2Provider()
