"""qBittorrent, behind the same interface as aria2.

This is the default downloader because it is the one that can be watched while
it is still arriving. aria2 1.37 has no sequential download for BitTorrent at
all — its `stream-piece-selector` is documented and implemented for HTTP and FTP
only — so with aria2 a file is unwatchable until the last piece lands. libtorrent,
which qBittorrent is built on, has both sequential download and
first-and-last-piece priority, and both are togglable **on a running torrent**.

That last part is why this replaces aria2 rather than sitting beside it. Watch
Now versus Download is a user's intent, and intent changes: you queue something
and then decide you want it tonight. With one client that is one API call. With
two clients it means stopping, deleting, and re-adding in the other one, losing
everything downloaded so far — a toggle turned into a restart. Two clients also
means two processes can end up writing the same file, which is corruption rather
than an inconvenience.

aria2 stays in the tree and is still selected by `MIRU_DOWNLOADER=aria2`, so
falling back is an environment variable rather than a migration.

Sequential mode is set per torrent from the button the user pressed, so a plain
Download still gets libtorrent's normal rarest-first piece selection.
"""

from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

from miru.acquisition.provider import AcquisitionError, DownloadJob, DownloadStatus
from miru.core.config import settings

log = logging.getLogger(__name__)


# qBittorrent authenticates with a session cookie rather than a token on each
# call, so the opener is shared and the login is re-run on a 403.
_lock = threading.Lock()
_opener: urllib.request.OpenerDirector | None = None

# A sleeping PC drops packets rather than refusing them, so a connect attempt
# costs the full timeout. Without remembering that, every caller pays it again:
# one poll over three past downloads was 45 seconds of a threadpool worker, and
# the UI polls faster than that drains. Two browser tabs then take the whole API
# down — including /api/health and library playback, which have nothing to do
# with the PC.
_failed_until = 0.0
_FAIL_TTL_S = 20.0
_TIMEOUT_S = 4.0


def _base() -> str:
    if not settings.qbittorrent_url:
        raise AcquisitionError("qBittorrent is not configured")
    return settings.qbittorrent_url.rstrip("/")


def _recently_failed() -> bool:
    return time.monotonic() < _failed_until


def _mark_failed() -> None:
    global _failed_until
    _failed_until = time.monotonic() + _FAIL_TTL_S


def _login() -> urllib.request.OpenerDirector:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))
    data = urllib.parse.urlencode(
        {"username": settings.qbittorrent_user, "password": settings.qbittorrent_password}
    ).encode()
    req = urllib.request.Request(
        f"{_base()}/api/v2/auth/login",
        data=data,
        # qBittorrent rejects cross-site requests without a matching Referer.
        headers={"Referer": _base()},
    )
    try:
        with opener.open(req, timeout=_TIMEOUT_S) as res:
            body = res.read().decode().strip()
    except OSError as exc:
        _mark_failed()
        raise AcquisitionError(f"Can't reach qBittorrent: {exc}") from exc
    if body != "Ok.":
        raise AcquisitionError("qBittorrent rejected the login")
    return opener


def _call(path: str, params: dict | None = None, *, retry: bool = True) -> str:
    global _opener
    if _recently_failed():
        raise AcquisitionError("qBittorrent is unreachable")

    # Logging in OUTSIDE the lock. Holding a process-wide lock across a network
    # call serialises every other request in the process behind a machine that
    # is asleep, which is how one slow call became an outage.
    with _lock:
        opener = _opener
    if opener is None:
        fresh = _login()
        with _lock:
            if _opener is None:
                _opener = fresh
            opener = _opener

    data = urllib.parse.urlencode(params).encode() if params else None
    req = urllib.request.Request(
        f"{_base()}/api/v2{path}", data=data, headers={"Referer": _base()}
    )
    try:
        with opener.open(req, timeout=_TIMEOUT_S) as res:
            return res.read().decode()
    except urllib.error.HTTPError as exc:
        if exc.code == 403 and retry:
            # Session expired. One re-login, then give up rather than looping.
            with _lock:
                _opener = None
            return _call(path, params, retry=False)
        raise AcquisitionError(f"qBittorrent said {exc.code}") from exc
    except OSError as exc:
        _mark_failed()
        raise AcquisitionError(f"Can't reach qBittorrent: {exc}") from exc


def _json(path: str, params: dict | None = None):
    raw = _call(path, params)
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AcquisitionError("qBittorrent returned something that is not JSON") from exc


def info_hash_of(magnet_or_hash: str) -> str:
    """The infohash out of whatever we were handed.

    Submissions are magnets built from an infohash, so this is normally just
    reading it back — but qBittorrent identifies torrents by hash, and the hash
    is what a job id has to be for status to survive an API restart.
    """
    if magnet_or_hash.startswith("magnet:"):
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(magnet_or_hash).query)
        for xt in qs.get("xt", []):
            if xt.startswith("urn:btih:"):
                return xt.split(":")[-1].lower()
        raise AcquisitionError("That magnet carries no infohash")
    return magnet_or_hash.strip().lower()


class QBittorrentProvider:
    """Downloads only. Search stays with Prowlarr."""

    def configured(self) -> bool:
        return bool(settings.qbittorrent_url)

    def submit(self, magnet: str, *, sequential: bool = False) -> DownloadJob:
        """Add a torrent. `sequential` is what makes it watchable while it lands.

        The job id is the infohash rather than anything qBittorrent invents, so
        a restart on either side does not orphan an in-flight download.
        """
        if not magnet or not magnet.startswith(("magnet:", "http://", "https://")):
            raise AcquisitionError("That is not something that can be downloaded")

        # Refused before anything is sent, on purpose. qBittorrent will add a
        # .torrent by URL quite happily but never reports the infohash back,
        # and the infohash is what the job id has to be — so every later
        # status, pause and cancel would look up a torrent it has never heard
        # of. The download would run, invisibly, while the UI called it failed
        # for the rest of its life. Adding it first and refusing afterwards
        # would be the same failure one step later.
        #
        # Unexercised in practice: all four configured indexers put a magnet in
        # magnetUrl or guid, and all 945 catalogue releases carry an infohash.
        # An indexer that only offers .torrent files would need the file
        # fetched and its info dict hashed; this is where that goes.
        if not magnet.startswith("magnet:"):
            raise AcquisitionError(
                "That indexer only offers a .torrent file, and Miru needs a "
                "magnet link to be able to follow the download afterwards."
            )

        params = {"urls": magnet, "sequentialDownload": "true" if sequential else "false"}
        if sequential:
            # The tail carries the index in a badly muxed MP4, and without it
            # playback cannot start at all however much of the front has landed.
            params["firstLastPiecePrio"] = "true"
        if settings.qbittorrent_save_path:
            params["savepath"] = settings.qbittorrent_save_path

        body = _call("/torrents/add", params)
        if body.strip() not in ("Ok.", ""):
            raise AcquisitionError(f"qBittorrent refused the torrent: {body[:120]}")

        return DownloadJob(id=info_hash_of(magnet), result_id=magnet)

    def statuses(self) -> dict[str, DownloadStatus]:
        """Every download qBittorrent knows about, by infohash.

        One call for the whole poll. The downloads screen used to ask once per
        work every few seconds, which is a request per card against a machine
        that is often asleep — and it could only ask about downloads a work
        still points at. A work has one download_job_id slot, so when
        enrichment merged two cards that each had a download in flight, one
        infohash was dropped and that download vanished from the screen with no
        progress, no pause and no cancel while it carried on downloading.
        Asking qBittorrent what it is actually doing cannot lose one.
        """
        out: dict[str, DownloadStatus] = {}
        for t in _json("/torrents/info") or []:
            h = str(t.get("hash") or "").lower()
            if h:
                out[h] = self._status_of(h, t)
        return out

    def status(self, job_id: str) -> DownloadStatus:
        rows = _json("/torrents/info", {"hashes": job_id.lower()})
        if not rows:
            raise AcquisitionError("qBittorrent has no such torrent")
        return self._status_of(job_id, rows[0])

    @staticmethod
    def _status_of(job_id: str, t: dict) -> DownloadStatus:
        total = int(t.get("size") or 0)
        done = int(t.get("completed") or 0)
        speed = int(t.get("dlspeed") or 0)
        eta = t.get("eta")

        return DownloadStatus(
            id=job_id,
            state=_STATES.get(t.get("state", ""), "downloading"),
            progress=float(t.get("progress") or 0.0),
            name=t.get("name"),
            downloaded_bytes=done,
            total_bytes=total,
            speed_bps=speed,
            # qBittorrent reports 8640000 (100 days) to mean "no idea", which is
            # worse than saying nothing.
            eta_seconds=int(eta) if isinstance(eta, int) and 0 < eta < 8640000 else None,
            # qBittorrent's error states carry no message over the API, but
            # they have well-known causes — and "failed, error: None" told the
            # user nothing while a stale NFS mount held every download at 0%.
            error=_ERROR_CAUSE.get(t.get("state", "")),
        )

    def cancel(self, job_id: str, delete_files: bool = False) -> None:
        # deleteFiles defaults to false: cancelling a download must not
        # silently remove a file the user may be mid-watch on. Only the
        # ephemeral janitor opts in — a stream nobody kept is the one thing
        # whose bytes exist to be thrown away.
        _call(
            "/torrents/delete",
            {"hashes": job_id.lower(), "deleteFiles": "true" if delete_files else "false"},
        )

    def set_paused(self, job_id: str, paused: bool) -> None:
        """Pause or resume. qBittorrent renamed these endpoints in v5, so both
        spellings are tried rather than pinning a version."""
        h = job_id.lower()
        primary = "/torrents/stop" if paused else "/torrents/start"
        legacy = "/torrents/pause" if paused else "/torrents/resume"
        try:
            _call(primary, {"hashes": h})
        except AcquisitionError:
            _call(legacy, {"hashes": h})

    def make_sequential(self, job_id: str) -> None:
        """Turn a running download into a watchable one.

        This is the whole argument for one client instead of two: changing your
        mind costs an API call rather than losing your progress.
        """
        h = job_id.lower()
        _call("/torrents/toggleSequentialDownload", {"hashes": h})
        _call("/torrents/toggleFirstLastPiecePrio", {"hashes": h})

    def files(self, job_id: str) -> list[dict]:
        return _json("/torrents/files", {"hash": job_id.lower()})

    def playable_prefix(self, job_id: str) -> dict:
        """How much of the largest file is contiguously readable from byte zero.

        This is what makes watch-while-downloading honest rather than hopeful.
        Progress alone is not enough: 40% downloaded says nothing about *which*
        40%, and with rarest-first piece order the first byte may be missing
        while the last is present. Only a run of completed pieces starting at
        piece zero is safe to serve.

        qBittorrent reports piece state per torrent (0 = not downloaded,
        1 = requested, 2 = downloaded), so the contiguous prefix is the count of
        leading 2s times the piece length. That count is measured from the start
        of the TORRENT, and in a multi-file torrent the video does not start
        there — every byte of every file ahead of it sits in front. Its own
        offset has to come off the top, or the ceiling lets the player read past
        what exists and it gets zeros, which decode as corruption rather than as
        an error anyone can see.
        """
        h = job_id.lower()
        rows = _json("/torrents/info", {"hashes": h})
        if not rows:
            raise AcquisitionError("qBittorrent has no such torrent")
        t = rows[0]

        files = _json("/torrents/files", {"hash": h}) or []
        # The video is the biggest file. Samples, subtitles and NFOs are not.
        biggest = max(files, key=lambda f: int(f.get("size") or 0), default=None)
        if biggest is None:
            raise AcquisitionError("That torrent has no files yet")

        states = _json("/torrents/pieceStates", {"hash": h}) or []
        props = _json("/torrents/properties", {"hash": h}) or {}
        piece_len = int(props.get("piece_size") or 0)

        ready = 0
        for s in states:
            if s != 2:
                break
            ready += 1

        prefix_bytes = ready * piece_len

        # Files are laid end to end in the torrent's byte stream in index
        # order, so the video's offset is the sum of everything before it.
        size = int(biggest.get("size") or 0)
        offset = sum(
            int(f.get("size") or 0)
            for f in files
            if int(f.get("index", 0)) < int(biggest.get("index", 0))
        )
        # Clamped at both ends: below, because a prefix that has not yet reached
        # the video means nothing of it is readable, not a negative amount;
        # above, because the trailing files are downloading too and
        # Content-Length is built from this number.
        readable = max(0, min(prefix_bytes - offset, size))

        return {
            "info_hash": h,
            "name": t.get("name"),
            "file": biggest.get("name"),
            "size_bytes": size,
            "save_path": t.get("save_path") or t.get("content_path") or "",
            "content_path": t.get("content_path") or "",
            "progress": float(t.get("progress") or 0.0),
            "playable_bytes": readable,
            "sequential": bool(t.get("seq_dl")),
            "complete": float(t.get("progress") or 0.0) >= 1.0,
        }

# qBittorrent's vocabulary is wider than ours and leaks implementation detail
# (three different flavours of "paused"). Translate rather than pass through.
# What qBittorrent's terse error states actually mean, in words a person can
# act on. `error` is almost always the save path being unwritable — measured
# here when the laptop's disk re-enumerated and the PC's NFS mount went stale.
_ERROR_CAUSE = {
    "error": (
        "qBittorrent can't write to its download folder. If the laptop's disk "
        "was reconnected, remount /mnt/incoming on the PC, then Resume."
    ),
    "missingFiles": (
        "The downloaded files are missing from the PC's download folder — "
        "check the mount, then Resume to re-download what's gone."
    ),
}

_STATES = {
    "error": "failed",
    "missingFiles": "failed",
    "uploading": "done",
    "stalledUP": "done",
    "queuedUP": "done",
    "checkingUP": "done",
    "forcedUP": "done",
    "pausedUP": "done",
    "stoppedUP": "done",
    "allocating": "queued",
    "downloading": "downloading",
    "metaDL": "queued",
    "forcedMetaDL": "queued",
    "pausedDL": "paused",
    "stoppedDL": "paused",
    "queuedDL": "queued",
    "forcedDL": "downloading",
    "stalledDL": "downloading",
    "checkingDL": "downloading",
    "checkingResumeData": "queued",
    "moving": "done",
    "unknown": "failed",
}

provider = QBittorrentProvider()
