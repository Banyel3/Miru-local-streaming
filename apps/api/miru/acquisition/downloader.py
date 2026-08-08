"""Which downloader is in use.

Search is always Prowlarr. Downloading is qBittorrent by default and aria2 by
configuration, because the two differ in exactly one way that matters: aria2
1.37 has no sequential download for BitTorrent, so a file it is fetching cannot
be watched until the last piece lands.

Resolved per call rather than bound at import, so tests and a running server can
both change it without reloading modules.
"""

from __future__ import annotations

from miru.core.config import settings


def downloader():
    """The configured download backend."""
    if settings.downloader == "aria2":
        from miru.acquisition.prowlarr import provider

        return provider

    from miru.acquisition.qbittorrent import provider

    return provider


def supports_streaming() -> bool:
    """Whether the configured backend can produce a watchable partial file.

    The UI needs this to decide whether Watch Now can mean "in a moment" or has
    to mean "when it finishes", and promising the first while doing the second
    is the failure this whole feature has refused twice.
    """
    return settings.downloader != "aria2"


def configured() -> bool:
    """Whether a downloader has been set up at all.

    Distinct from reachable() on purpose, and the distinction is one this
    project has already learned once with the transcode worker: "the PC is
    asleep" and "you never installed this" are different problems with
    different fixes, and collapsing them sends the user to wake a machine that
    is already awake.
    """
    try:
        return downloader().configured()
    except Exception:  # noqa: BLE001
        return False


def reachable() -> bool:
    """Whether the download backend is answering right now."""
    try:
        d = downloader()
        if not d.configured():
            return False
        if settings.downloader == "aria2":
            from miru.acquisition.prowlarr import _rpc

            _rpc("aria2.getVersion", [])
        else:
            from miru.acquisition.qbittorrent import _call

            _call("/app/version")
        return True
    except Exception:  # noqa: BLE001 — unreachable is the answer, not an error
        return False
