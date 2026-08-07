"""Playback strategy resolution — ARCHITECTURE.md §2.

Pure functions over a Probe. No I/O, no ffmpeg, no database. That means the
whole ladder is testable without a media file, and re-resolving the library
after a rule change is an UPDATE loop rather than a rescan.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# What a browser can play without help. Deliberately conservative: guessing
# wrong here means a black player, and the cost of being wrong the other way
# is one remux.
VIDEO_OK = {"h264", "vp8", "vp9", "av1"}
AUDIO_OK = {"aac", "mp3", "opus", "vorbis", "flac"}
CONTAINER_OK = {"mp4", "m4v", "mov", "webm"}

DIRECT = "direct"
REMUX = "remux"
TRANSCODE_AUDIO = "transcode_audio"
TRANSCODE_FULL = "transcode_full"


@dataclass
class Probe:
    duration_ms: int | None = None
    container: str | None = None
    video_codec: str | None = None
    audio_codec: str | None = None
    audio_channels: int | None = None
    width: int | None = None
    height: int | None = None
    subtitle_streams: list[dict] = field(default_factory=list)


def resolve_strategy(p: Probe) -> str:
    """Lowest rung of the ladder that will play this file."""
    if p.video_codec is None:
        # Unprobed or audio-only. Serve it and let the browser decide; a wrong
        # `direct` costs one failed play, a wrong `transcode_full` costs GPU
        # time on every request.
        return DIRECT

    if p.video_codec not in VIDEO_OK:
        return TRANSCODE_FULL

    # Video is fine from here down. Subtitles never enter this decision —
    # ASS/SSA is extracted and rendered client-side by JASSUB.
    #
    # No audio stream at all is not an audio problem: silent video, or a track
    # ripped without audio, plays directly. Treating a missing codec as a bad
    # codec sends those files to the transcoder for a stream that isn't there.
    if p.audio_codec is None:
        audio_ok = True
    else:
        audio_ok = p.audio_codec in AUDIO_OK and (p.audio_channels or 2) <= 2
    container_ok = (p.container or "") in CONTAINER_OK

    if not audio_ok:
        return TRANSCODE_AUDIO
    if not container_ok:
        return REMUX
    return DIRECT


def _container_from(format_name: str, path: Path) -> str:
    # ffprobe reports comma-joined guesses ("mov,mp4,m4a,3gp,3g2,mj2").
    # The extension is the tiebreaker that matches how browsers sniff.
    names = format_name.split(",")
    ext = path.suffix.lstrip(".").lower()
    return ext if ext in names else names[0]


def probe_file(path: Path) -> Probe:
    """Read stream info via ffprobe. Returns an empty Probe if ffprobe is
    missing or the file is unreadable — a file we can't probe is still a file
    worth listing."""
    if not shutil.which("ffprobe"):
        return Probe()

    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json",
             "-show_format", "-show_streams", str(path)],
            capture_output=True, text=True, timeout=60, check=True,
        ).stdout
        data = json.loads(out)
    except (subprocess.SubprocessError, json.JSONDecodeError, OSError):
        return Probe()

    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio = next((s for s in streams if s.get("codec_type") == "audio"), {})

    duration = fmt.get("duration")
    return Probe(
        duration_ms=int(float(duration) * 1000) if duration else None,
        container=_container_from(fmt.get("format_name", ""), path),
        video_codec=video.get("codec_name"),
        audio_codec=audio.get("codec_name"),
        audio_channels=audio.get("channels"),
        width=video.get("width"),
        height=video.get("height"),
        subtitle_streams=[
            {
                "index": s.get("index"),
                "codec": s.get("codec_name"),
                "language": s.get("tags", {}).get("language"),
                "title": s.get("tags", {}).get("title"),
            }
            for s in streams
            if s.get("codec_type") == "subtitle"
        ],
    )
