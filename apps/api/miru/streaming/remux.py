"""Making an MKV playable, on the laptop.

The `remux` rung has existed in the ladder since M1 and nothing ever
implemented it. `/api/stream/{id}` served the raw Matroska bytes while the web
client's `mimeType()` claimed `video/mp4`, so every H.264-in-MKV file — the
majority rung of an anime library — showed a black player. That is the bug this
file closes.

It runs on the laptop, deliberately. Remuxing is stream-copy, not encoding:
DEPLOYMENT.md §4 measured 0.37s of CPU for a ten-minute film, and that document
lists "remux stays on the laptop" among the decisions not to re-litigate,
because sending it to the PC would make the PC a hard dependency for most of the
collection and defeat the point of the two-box layout. The PC is for the GPU.

The output is a real file with `+faststart`, not a pipe or a segment stream.
That is the laziest thing that is also the most correct: seeking works because
the moov atom is at the front and the file is served by the same tested Range
path as everything else, so no new streaming code exists to get wrong.
"""

from __future__ import annotations

import contextlib
import hashlib
import time
import logging
import shutil
import subprocess
import threading
from pathlib import Path

from miru.core.config import settings

log = logging.getLogger(__name__)

# One remux at a time. Two ffmpeg processes stream-copying gigabyte files fight
# over the same disk, and the second gains nothing by starting early.
_lock = threading.Lock()
_running: dict[int, threading.Thread] = {}
_failed: dict[int, str] = {}


def cache_dir() -> Path:
    root = Path(settings.remux_cache_path or "/tmp/miru-remux")
    root.mkdir(parents=True, exist_ok=True)
    return root


def cached_path(file_id: int, source: Path, prefix_bytes: int | None = None) -> Path:
    """Where the playable copy of this file lives.

    Keyed on size and mtime as well as the id, so replacing a file on disk with
    a different one under the same name cannot serve the old remux forever.

    `prefix_bytes` is accepted and deliberately ignored. It used to be part of
    the key, on the reasoning that a longer prefix must not be served the
    shorter remux — and on a growing file that made every request a different
    key, so every request missed, every miss started a fresh gigabyte-scale
    ffmpeg, and the remux that finished was keyed to a prefix that had already
    moved on. Measured live: six requests, six 503s, a complete 1.34 GB remux on
    disk unserved, and a second ffmpeg redoing it.

    The output grows instead. One file, one identity, one job — and the caller
    reads however much of it exists.
    """
    try:
        stat = source.stat()
        stamp = f"{stat.st_size}-{int(stat.st_mtime)}"
    except OSError:
        stamp = "0-0"
    digest = hashlib.sha256(f"{file_id}:{stamp}".encode()).hexdigest()[:20]
    return cache_dir() / f"{digest}.mp4"


def command(source: Path, dest: Path, *, fragmented: bool = False, limit_bytes: int | None = None) -> list[str]:
    """The ffmpeg call. Split out so the flags can be asserted without running it.

    `fragmented` writes a file that is playable from byte zero with no index at
    the end — which is the only way to remux something still downloading.
    `+faststart` moves the index to the front *after* writing the whole file,
    so on a prefix there is nothing to move and nothing to move it from.
    """
    movflags = (
        "+frag_keyframe+empty_moov+default_base_moof" if fragmented else "+faststart"
    )
    # Fed on stdin when only part of the file exists. A sequential torrent is
    # not short — Watch Now asks for firstLastPiecePrio, so the last piece lands
    # almost immediately and the file is already its final size with a hole in
    # the middle. Pointed at it, ffmpeg reads zeros, and zeros decode as
    # corruption rather than as an error anyone can see. A pipe cannot seek,
    # which is fine: a fragmented remux never needs to.
    source_arg = "pipe:0" if limit_bytes is not None else str(source)
    return [
        "ffmpeg", "-nostdin", "-v", "error", "-y",
        "-i", source_arg,
        # Video and audio are already what a browser wants; only the container
        # is wrong. Subtitles are deliberately dropped: MP4 cannot carry ASS,
        # and they are served separately as tracks the player renders itself.
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c", "copy",
        "-movflags", movflags,
        str(dest),
    ]


def read_prefix(source: Path, limit: int, chunk: int = 1 << 20):
    """The first `limit` bytes of a file, or all of it if it is shorter.

    Shorter is the normal disagreement, not an error: the piece report and the
    filesystem are two machines' opinions and the smaller one is the safe one.
    """
    left = limit
    with source.open("rb") as fh:
        while left > 0:
            block = fh.read(min(chunk, left))
            if not block:
                return
            left -= len(block)
            yield block


def follow(source: Path, ceiling, alive, chunk: int = 1 << 20, poll: float = 1.0):
    """The source's bytes as they arrive, never past the completed prefix.

    One ffmpeg is fed this for the life of a download, rather than one started
    per request over a prefix that had grown. `ceiling()` is asked again on each
    pass because the answer changes as pieces land, and it is a callable rather
    than a number for exactly that reason.

    Reading past the ceiling is the thing this exists to prevent: a sequential
    torrent file is full size from the start with a hole in the middle — Watch
    Now asks for firstLastPiecePrio, so the last piece arrives almost
    immediately — and a hole reads as zeros, which decode as corruption rather
    than as an error anyone can see.

    `alive()` ends it. Without that the generator blocks forever on a cancelled
    or stalled download and holds an ffmpeg per abandoned session.
    """
    sent = 0
    with source.open("rb") as fh:
        while True:
            available = max(0, ceiling() - sent)
            if available <= 0:
                if not alive():
                    return
                time.sleep(poll)
                continue
            block = fh.read(min(chunk, available))
            if not block:
                # The filesystem has not caught up with the piece report. They
                # are two machines' opinions and the smaller one is the safe one.
                if not alive():
                    return
                time.sleep(poll)
                continue
            sent += len(block)
            yield block


def adopt(info_hash: str, file_id: int, library_path: Path) -> bool:
    """Rename a finished live remux to the library's key. True if it happened.

    The live path keys on the infohash, the library on the MediaFile id — so a
    film watched while downloading used to be remuxed AGAIN on first library
    play, and the cache held three copies of one title (~6 GB, measured). The
    mover preserves size and mtime, so the live digest is recomputable from the
    library file's own stat, and the handover is one rename.

    Refuses when the live remux is still being written (renaming under ffmpeg
    corrupts both copies) and never clobbers an existing library remux — if the
    user played it before promotion linked it, the library copy is newer.
    """
    # Imported here rather than at module top: partial.py imports this module.
    from miru.streaming.partial import info_hash_key

    live = cached_path(info_hash_key(info_hash), library_path)
    dest = cached_path(file_id, library_path)
    if live.with_suffix(".part.mp4").exists():
        return False
    if not live.exists() or dest.exists():
        return False
    live.replace(dest)
    log.info("adopted live remux for %s as %s", library_path.name, dest.name)
    return True


def progress(file_id: int, source: Path) -> float | None:
    """How far a running remux has got, 0..1, or None when nothing is written.

    The .part file's size against the source's. A stream-copy remux tracks its
    input closely enough for a progress bar — this is a progress bar, not an
    ETA contract. The number existed on disk the whole time a 3.8 GB film spent
    seven silent minutes behind a bare spinner; nothing surfaced it.
    """
    part = cached_path(file_id, source).with_suffix(".part.mp4")
    try:
        written = part.stat().st_size
        total = source.stat().st_size
    except OSError:
        return None
    if not total:
        return None
    return min(1.0, written / total)


def state(file_id: int, source: Path, prefix_bytes: int | None = None) -> str:
    """`ready` | `working` | `failed` | `absent`."""
    if cached_path(file_id, source, prefix_bytes).exists():
        return "ready"
    with _lock:
        key = _key(file_id, prefix_bytes)
        if key in _running and _running[key].is_alive():
            return "working"
        if key in _failed:
            return "failed"
    return "absent"


def _key(file_id: int, prefix_bytes: int | None = None) -> object:
    """What counts as "the same job". One per file.

    It was once per (file, prefix), which meant a second ffmpeg could start on
    the same source while the first was still running — and on a growing file
    that happened on every poll.
    """
    return file_id


# Outputs currently being written. Reaped `.part` files are the wreckage of the
# runaway loop above; the one a live remux is writing must not be swept with it.
_active_parts: set[Path] = set()


def reap() -> int:
    """Delete half-written outputs nothing is working on. Returns how many.

    A `.part` file is a remux that died — the process was killed, the API
    restarted, or the loop that used to start one per request moved on. They are
    full-size, so leaving them costs real disk: 4.8 GB had accumulated when this
    was found.
    """
    gone = 0
    with _lock:
        live = set(_active_parts)
    for part in cache_dir().glob("*.part.mp4"):
        if part in live:
            continue
        try:
            part.unlink()
            gone += 1
        except OSError:  # noqa: PERF203 — one undeletable file must not stop the rest
            log.warning("could not reap %s", part.name)
    return gone


def forget(file_id: int) -> None:
    """Drop a recorded failure so this file may be attempted again."""
    with _lock:
        _failed.pop(_key(file_id), None)


def error(file_id: int, prefix_bytes: int | None = None) -> str | None:
    with _lock:
        return _failed.get(_key(file_id, prefix_bytes))


def _run(
    file_id: object,
    source: Path,
    dest: Path,
    fragmented: bool = False,
    limit_bytes=None,
    alive=None,
) -> None:
    # Written to a temporary name and renamed, so a half-written file is never
    # served as a complete one — the same discipline as the poster cache.
    tmp = dest.with_suffix(".part.mp4")
    with _lock:
        _active_parts.add(tmp)
    cmd = command(source, tmp, fragmented=fragmented, limit_bytes=limit_bytes)
    try:
        if limit_bytes is None:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            code, err = proc.returncode, proc.stderr or ""
        else:
            code, err = _feed(cmd, source, limit_bytes, alive)
        if code != 0 or not tmp.exists():
            tail = err.strip().splitlines()
            raise RuntimeError(tail[-1] if tail else f"ffmpeg exited {code}")
        tmp.replace(dest)
        with _lock:
            _failed.pop(file_id, None)
        log.info("remuxed %s -> %s", source.name, dest.name)
    except Exception as exc:  # noqa: BLE001 — recorded so the UI can say why
        tmp.unlink(missing_ok=True)
        with _lock:
            _failed[file_id] = str(exc)[:200]
        log.warning("remux failed for %s: %s", source.name, exc)
    finally:
        with _lock:
            _active_parts.discard(tmp)


def _feed(cmd: list[str], source: Path, limit, alive=None) -> tuple[int, str]:
    """Run ffmpeg with the source's bytes on its stdin.

    `limit` is either a byte count — a fixed prefix, used by the tests — or a
    callable giving the ceiling as it moves, which is how a live download is
    followed by one process instead of one per request.
    """
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    feed = (
        follow(source, limit, alive or (lambda: True))
        if callable(limit)
        else read_prefix(source, limit)
    )
    try:
        for block in feed:
            proc.stdin.write(block)
    except BrokenPipeError:
        # ffmpeg gave up early — its own stderr says why, and that is the
        # message worth reporting rather than this symptom of it.
        pass
    finally:
        with contextlib.suppress(OSError):
            proc.stdin.close()
    err = (proc.stderr.read() or b"").decode(errors="replace")
    return proc.wait(timeout=1800), err


def ensure(file_id: int, source: Path, prefix_bytes=None, alive=None) -> str:
    """Start a remux if one is needed. Returns the state after asking.

    `prefix_bytes` marks this as a still-downloading file: the output is
    fragmented so it plays from byte zero without an index at the end, and the
    cache entry is per-prefix so a later, longer request is a miss rather than
    a stale hit.
    """
    if not shutil.which("ffmpeg"):
        with _lock:
            _failed[_key(file_id, prefix_bytes)] = "ffmpeg is not installed on this machine."
        return "failed"

    current = state(file_id, source, prefix_bytes)
    if current in ("ready", "working"):
        return current
    if current == "failed":
        # Not retried here. A source ffmpeg cannot read fails the same way every
        # time, and the player polls — so retrying on each request is the same
        # runaway that keying on the prefix produced. Cleared by `forget()` when
        # something has actually changed.
        return "failed"

    dest = cached_path(file_id, source, prefix_bytes)
    with _lock:
        # Re-checked under the lock: two requests arriving together must not
        # start two ffmpeg processes over the same gigabyte.
        key = _key(file_id, prefix_bytes)
        if key in _running and _running[key].is_alive():
            return "working"
        _failed.pop(key, None)
        t = threading.Thread(
            target=_run,
            args=(key, source, dest, prefix_bytes is not None, prefix_bytes, alive),
            daemon=True,
        )
        _running[key] = t
        t.start()
    return "working"


def evict(keep_bytes: int | None = None) -> int:
    """Drop the least recently used remuxes. Returns bytes freed.

    A remux is the same size as its source, so an unbounded cache doubles the
    library. Nothing here is precious: every file can be rebuilt in seconds.
    """
    limit = keep_bytes if keep_bytes is not None else settings.remux_cache_bytes
    if limit <= 0:
        return 0
    files = sorted(cache_dir().glob("*.mp4"), key=lambda f: f.stat().st_atime)
    total = sum(f.stat().st_size for f in files)
    freed = 0
    for f in files:
        if total - freed <= limit:
            break
        size = f.stat().st_size
        f.unlink(missing_ok=True)
        freed += size
    return freed
