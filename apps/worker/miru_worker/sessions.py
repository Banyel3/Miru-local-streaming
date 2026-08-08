"""Encode session lifecycle: start ffmpeg, wait for a playable manifest, reap."""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from miru_worker.config import settings
from miru_worker.ladder import Rendition, build_command, renditions_for, session_id

log = logging.getLogger(__name__)


@dataclass
class Session:
    sid: str
    src: str
    dir: Path
    renditions: list[Rendition]
    process: subprocess.Popen | None = None
    started_at: float = field(default_factory=time.time)

    @property
    def master(self) -> Path:
        return self.dir / "master.m3u8"

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def stop(self) -> None:
        if self.alive and self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()


_sessions: dict[str, Session] = {}
_locks: dict[str, asyncio.Lock] = {}


def detect_encoder() -> str:
    """NVENC only if it genuinely runs. The encoder can be listed by a build and
    still fail at runtime when the driver is missing — which is exactly the
    situation on a machine with no NVIDIA card, and exactly the case that must
    fall back rather than crash on first playback."""
    if settings.encoder:
        return settings.encoder
    if not shutil.which("ffmpeg"):
        return "libx264"

    probe = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", "testsrc=size=128x128:rate=1", "-t", "0.1",
         "-c:v", "h264_nvenc", "-f", "null", "-"],
        capture_output=True, timeout=60,
    )
    return "h264_nvenc" if probe.returncode == 0 else "libx264"


ENCODER = detect_encoder()


def _lock_for(sid: str) -> asyncio.Lock:
    return _locks.setdefault(sid, asyncio.Lock())


async def ensure(src: str, source_height: int | None, copy_video: bool) -> Session:
    """Return a session with a playable manifest, starting one if needed.

    The per-sid lock is what makes a burst of requests for the same file start
    exactly one ffmpeg instead of one per request.
    """
    sid = session_id(src, source_height, copy_video)

    async with _lock_for(sid):
        existing = _sessions.get(sid)
        if existing and existing.master.exists():
            return existing
        if existing and existing.alive:
            await _await_master(existing)
            return existing

        _evict_if_needed()

        out = settings.cache / sid
        out.mkdir(parents=True, exist_ok=True)
        renditions = [] if copy_video else renditions_for(source_height)
        # ffmpeg does not create the %v directories itself.
        for i in range(max(1, len(renditions))):
            (out / str(i)).mkdir(exist_ok=True)

        cmd = build_command(src, out, renditions, ENCODER, copy_video)
        log.info("starting encode %s (%s, %d renditions)", sid, ENCODER, max(1, len(renditions)))
        proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        session = Session(sid=sid, src=src, dir=out, renditions=renditions, process=proc)
        _sessions[sid] = session
        await _await_master(session)
        return session


async def _await_master(session: Session) -> None:
    """Wait for the master playlist and the first segment. Serving a manifest
    that references segments which do not exist yet makes the player give up."""
    deadline = time.time() + settings.startup_timeout_s
    while time.time() < deadline:
        if session.master.exists() and any(session.dir.rglob("seg0.ts")):
            return
        if not session.alive:
            err = b""
            if session.process and session.process.stderr:
                err = session.process.stderr.read() or b""
            raise RuntimeError(
                f"ffmpeg exited before producing a manifest: {err.decode('utf-8', 'replace')[-500:]}"
            )
        await asyncio.sleep(0.25)
    raise TimeoutError(f"no manifest after {settings.startup_timeout_s}s")


def _evict_if_needed() -> None:
    for sid, s in list(_sessions.items()):
        if not s.alive and time.time() - s.started_at > settings.session_ttl_hours * 3600:
            _drop(sid)

    while len(_sessions) >= settings.max_sessions:
        oldest = min(_sessions.values(), key=lambda s: s.started_at)
        log.info("evicting session %s to stay under max_sessions", oldest.sid)
        _drop(oldest.sid)


def _drop(sid: str) -> None:
    session = _sessions.pop(sid, None)
    if not session:
        return
    session.stop()
    shutil.rmtree(session.dir, ignore_errors=True)
    _locks.pop(sid, None)


def active() -> list[Session]:
    return list(_sessions.values())


def shutdown() -> None:
    for sid in list(_sessions):
        _drop(sid)
