"""Client for the transcode worker on the PC — ARCHITECTURE.md, DEPLOYMENT.md §6.

The API never transcodes. It decides *whether* a file needs an encoder and, if
so, hands the worker a URL. Everything here is about that handoff and about
knowing whether the PC is reachable.
"""

from __future__ import annotations

import json
import logging
import time
import urllib.error
import urllib.request
from urllib.parse import urlencode

from miru.core.config import settings

log = logging.getLogger(__name__)

# Rungs that require an encoder to run. These are the ones that need the PC.
NEEDS_WORKER = {"transcode_audio", "transcode_full"}

# Health is cached so the library page never pays for a network round trip per
# file, and never blocks on a machine that is asleep.
_CACHE_TTL_S = 10.0
_PROBE_TIMEOUT_S = 1.0  # an HTTP round trip, not just a handshake

_last_checked = 0.0
_last_result = True  # fail OPEN: a flaky probe must not hide a playable file


def worker_configured() -> bool:
    return bool(settings.transcode_worker)


def worker_up() -> bool:
    """Cached check that the worker — specifically — is answering.

    This asks /health and requires the worker's own response shape. A bare TCP
    connect is faster but proves only that *something* holds the port: a stray
    dev server, or another service that claimed it after a reboot, would pass and
    Miru would promise playback that then fails. Caught exactly that in testing
    against a leftover `python -m http.server`.
    """
    global _last_checked, _last_result

    if not worker_configured():
        return False

    now = time.monotonic()
    if now - _last_checked < _CACHE_TTL_S:
        return _last_result

    url = f"{settings.transcode_worker.rstrip('/')}/health"
    try:
        with urllib.request.urlopen(url, timeout=_PROBE_TIMEOUT_S) as resp:
            body = json.loads(resp.read(512))
        _last_result = bool(body.get("ok")) and "encoder" in body
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        _last_result = False
    _last_checked = now
    return _last_result


def availability(strategy: str) -> tuple[str, str | None]:
    """(state, note) for the UI — derived, never stored.

    A stored value would go stale the moment the PC came back, and the whole
    point is that the answer changes without the library changing.
    """
    if strategy not in NEEDS_WORKER:
        return "available", None
    if not worker_configured():
        return "unavailable", "This file needs transcoding, which isn't configured yet."
    if not worker_up():
        return "unavailable", "Needs the PC to transcode — it's offline."
    return "gpu-ready", None


def hls_url(file_id: int, strategy: str, height: int | None) -> str:
    """Where the browser should fetch HLS from.

    The player is sent to the worker directly rather than proxied through here:
    segments are the bulk of the bytes, and routing them through the laptop
    would double their trip for no benefit. The client is already on the tailnet
    or it could not have reached Miru at all.
    """
    source = f"{settings.public_api_url.rstrip('/')}/api/stream/{file_id}"
    params = {"src": source, "copy_video": str(strategy == "transcode_audio").lower()}
    if height:
        params["height"] = str(height)
    return f"{settings.transcode_worker.rstrip('/')}/hls?{urlencode(params)}"
