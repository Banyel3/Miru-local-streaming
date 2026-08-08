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

    `prefix_bytes` is how much of a still-downloading file was readable when
    this copy was made, and it is part of the key. A growing file keeps its name
    and its inode, so without it the remux of the first 200 MB would be served
    for the rest of the download and the video would stop there forever.
    """
    try:
        stat = source.stat()
        stamp = f"{stat.st_size}-{int(stat.st_mtime)}"
    except OSError:
        stamp = "0-0"
    if prefix_bytes is not None:
        stamp = f"{stamp}@{prefix_bytes}"
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


def _key(file_id: int, prefix_bytes: int | None) -> object:
    """What counts as "the same job".

    Per prefix, not per file. A growing file is remuxed again at each new
    prefix, and sharing one key would report the earlier, shorter job as this
    one's and never start the longer.
    """
    return file_id if prefix_bytes is None else (file_id, prefix_bytes)


def error(file_id: int, prefix_bytes: int | None = None) -> str | None:
    with _lock:
        return _failed.get(_key(file_id, prefix_bytes))


def _run(
    file_id: object,
    source: Path,
    dest: Path,
    fragmented: bool = False,
    limit_bytes: int | None = None,
) -> None:
    # Written to a temporary name and renamed, so a half-written file is never
    # served as a complete one — the same discipline as the poster cache.
    tmp = dest.with_suffix(".part.mp4")
    cmd = command(source, tmp, fragmented=fragmented, limit_bytes=limit_bytes)
    try:
        if limit_bytes is None:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
            code, err = proc.returncode, proc.stderr or ""
        else:
            code, err = _feed(cmd, source, limit_bytes)
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


def _feed(cmd: list[str], source: Path, limit: int) -> tuple[int, str]:
    """Run ffmpeg with the prefix on its stdin."""
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        for block in read_prefix(source, limit):
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


def ensure(file_id: int, source: Path, prefix_bytes: int | None = None) -> str:
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
            args=(key, source, dest, prefix_bytes is not None, prefix_bytes),
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
