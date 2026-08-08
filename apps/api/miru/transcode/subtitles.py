"""Subtitle extraction — ARCHITECTURE.md §2.

Subtitles never touch the video stream. Extracting a text track is a copy for
ASS/SSA and a text conversion for SRT; neither re-encodes a frame, so a file
with awkward subtitles still direct plays.

Extraction is cached on disk because it costs an ffmpeg process and a read
across `/mnt/`, and the answer never changes for a given file and track.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

# Formats JASSUB renders with full styling, versus ones the browser can take
# natively once converted to WebVTT.
ASS_CODECS = {"ass", "ssa"}
TEXT_CODECS = {"subrip", "srt", "webvtt", "vtt", "mov_text", "text"}

SIDECAR_EXTENSIONS = {".ass", ".ssa", ".srt", ".vtt"}

# ffmpeg's encoder names are not the file extensions. Passing the extension
# straight through asked for "-c:s vtt", which ffmpeg answers with
# "Unknown encoder 'vtt'" — so every subtitle request 422'd and captions never
# worked anywhere in the app.
_ENCODERS = {"vtt": "webvtt", "ass": "ass", "ssa": "ass", "srt": "srt"}


class SubtitleError(RuntimeError):
    pass


def format_for(codec: str | None) -> str:
    """The format this track will be served as: 'ass' keeps styling, 'vtt' is
    what the browser can render without help."""
    return "ass" if (codec or "").lower() in ASS_CODECS else "vtt"


def _run(args: list[str]) -> None:
    if not shutil.which("ffmpeg"):
        raise SubtitleError("ffmpeg is not installed")
    try:
        subprocess.run(args, capture_output=True, timeout=120, check=True)
    except subprocess.CalledProcessError as exc:
        raise SubtitleError(exc.stderr.decode("utf-8", "replace")[-400:]) from exc
    except subprocess.SubprocessError as exc:
        raise SubtitleError(str(exc)) from exc


def extract_embedded(source: Path, stream_index: int, dest: Path) -> Path:
    """Pull one subtitle track out of the container.

    `-map 0:{index}` addresses the stream by its absolute index, which is what
    ffprobe reported at scan time, so the track the UI lists is the track that
    comes out.
    """
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(source),
         "-map", f"0:{stream_index}", "-c:s", _ENCODERS.get(dest.suffix.lstrip("."), dest.suffix.lstrip(".")),
         str(dest)]
    )
    if not dest.exists() or dest.stat().st_size == 0:
        raise SubtitleError(f"stream {stream_index} produced no subtitle output")
    return dest


def convert_sidecar(source: Path, dest: Path) -> Path:
    """Convert an external subtitle file to the served format. ASS to ASS is a
    copy; SRT to VTT is a text conversion. Either way the video is untouched."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix.lower() == dest.suffix.lower():
        shutil.copyfile(source, dest)
        return dest
    _run(["ffmpeg", "-y", "-v", "error", "-i", str(source), str(dest)])
    if not dest.exists() or dest.stat().st_size == 0:
        raise SubtitleError(f"could not convert {source.name}")
    return dest


def find_sidecars(video: Path) -> list[dict]:
    """Subtitle files sitting next to the video.

    Anime libraries routinely ship subtitles beside the video rather than muxed
    in, often several languages deep, so a scanner that only reads embedded
    streams misses most of them. Matches `Episode.ass`, `Episode.en.ass`,
    `Episode.eng.forced.ass`.
    """
    stem = video.stem
    found: list[dict] = []
    try:
        neighbours = sorted(video.parent.iterdir())
    except OSError:
        return found

    for path in neighbours:
        if path.suffix.lower() not in SIDECAR_EXTENSIONS or not path.is_file():
            continue
        if path.stem != stem and not path.stem.startswith(f"{stem}."):
            continue
        # Everything between the video's stem and the extension is a label:
        # "Episode.eng.forced.ass" -> "eng.forced"
        tag = path.stem[len(stem) :].strip(".")
        found.append(
            {
                "index": None,
                "codec": path.suffix.lstrip(".").lower(),
                "language": tag.split(".")[0] or None,
                "title": tag or None,
                "source": "external",
                "path": str(path),
            }
        )
    return found
