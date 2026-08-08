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


def cached_path(file_id: int, source: Path) -> Path:
    """Where the playable copy of this file lives.

    Keyed on size and mtime as well as the id, so replacing a file on disk with
    a different one under the same name cannot serve the old remux forever.
    """
    try:
        stat = source.stat()
        stamp = f"{stat.st_size}-{int(stat.st_mtime)}"
    except OSError:
        stamp = "0-0"
    digest = hashlib.sha256(f"{file_id}:{stamp}".encode()).hexdigest()[:20]
    return cache_dir() / f"{digest}.mp4"


def state(file_id: int, source: Path) -> str:
    """`ready` | `working` | `failed` | `absent`."""
    if cached_path(file_id, source).exists():
        return "ready"
    with _lock:
        if file_id in _running and _running[file_id].is_alive():
            return "working"
        if file_id in _failed:
            return "failed"
    return "absent"


def error(file_id: int) -> str | None:
    with _lock:
        return _failed.get(file_id)


def _run(file_id: int, source: Path, dest: Path) -> None:
    # Written to a temporary name and renamed, so a half-written file is never
    # served as a complete one — the same discipline as the poster cache.
    tmp = dest.with_suffix(".part.mp4")
    cmd = [
        "ffmpeg", "-nostdin", "-v", "error", "-y",
        "-i", str(source),
        # Video and audio are already what a browser wants; only the container
        # is wrong. Subtitles are deliberately dropped: MP4 cannot carry ASS,
        # and they are served separately as tracks the player renders itself.
        "-map", "0:v:0", "-map", "0:a:0?",
        "-c", "copy",
        "-movflags", "+faststart",
        str(tmp),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
        if proc.returncode != 0 or not tmp.exists():
            tail = (proc.stderr or "").strip().splitlines()
            raise RuntimeError(tail[-1] if tail else f"ffmpeg exited {proc.returncode}")
        tmp.replace(dest)
        with _lock:
            _failed.pop(file_id, None)
        log.info("remuxed %s -> %s", source.name, dest.name)
    except Exception as exc:  # noqa: BLE001 — recorded so the UI can say why
        tmp.unlink(missing_ok=True)
        with _lock:
            _failed[file_id] = str(exc)[:200]
        log.warning("remux failed for %s: %s", source.name, exc)


def ensure(file_id: int, source: Path) -> str:
    """Start a remux if one is needed. Returns the state after asking."""
    if not shutil.which("ffmpeg"):
        with _lock:
            _failed[file_id] = "ffmpeg is not installed on this machine."
        return "failed"

    current = state(file_id, source)
    if current in ("ready", "working"):
        return current

    dest = cached_path(file_id, source)
    with _lock:
        # Re-checked under the lock: two requests arriving together must not
        # start two ffmpeg processes over the same gigabyte.
        if file_id in _running and _running[file_id].is_alive():
            return "working"
        _failed.pop(file_id, None)
        t = threading.Thread(target=_run, args=(file_id, source, dest), daemon=True)
        _running[file_id] = t
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
