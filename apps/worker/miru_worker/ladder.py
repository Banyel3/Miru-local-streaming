"""Rendition ladder and ffmpeg command construction.

Pure functions, no I/O and no subprocess — same reason `transcode/strategy.py`
is pure on the API side: the whole ladder is testable without a GPU, without
ffmpeg, and without a media file.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Rendition:
    name: str
    height: int
    bitrate_k: int

    @property
    def maxrate_k(self) -> int:
        return int(self.bitrate_k * 1.07)

    @property
    def bufsize_k(self) -> int:
        return self.bitrate_k * 2


# Audio is encoded identically in every variant on purpose. If the bitrate or
# channel layout differs between rungs, switching quality changes the audio
# configuration too, and the player has to re-initialise its audio decoder in
# the middle of playback. Keeping it constant means a switch only ever swaps the
# video track — which is the whole point of an ABR ladder.
AUDIO_BITRATE_K = 128


LADDER: tuple[Rendition, ...] = (
    Rendition("1080p", 1080, 6000),
    Rendition("720p", 720, 3000),
    Rendition("480p", 480, 1200),
)

SEGMENT_SECONDS = 4


def renditions_for(source_height: int | None) -> list[Rendition]:
    """Ladder capped by the source. Upscaling spends GPU time to make a file
    larger and no better, so the top rung is never above the source."""
    if not source_height:
        return [LADDER[1]]  # unknown source: one safe middle rung

    usable = [r for r in LADDER if r.height <= source_height]
    if not usable:
        # Source is smaller than every rung (a 360p clip). Encode at source.
        return [Rendition(f"{source_height}p", source_height, 1200)]
    return usable


def session_id(src: str, source_height: int | None, copy_video: bool) -> str:
    """Deterministic, so the endpoint is idempotent: two players asking for the
    same thing share one encode instead of starting two."""
    key = f"{src}|{source_height}|{'copy' if copy_video else 'encode'}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def is_allowed(src: str, prefixes: list[str]) -> bool:
    return any(src.startswith(p) for p in prefixes)


def build_command(
    src: str,
    out_dir: Path,
    renditions: list[Rendition],
    encoder: str,
    copy_video: bool = False,
) -> list[str]:
    """One ffmpeg process producing a master playlist and every variant.

    `copy_video` is the transcode_audio case: the video stream passes through
    untouched, so there is exactly one rendition — there is nothing to build
    variants from.
    """
    base = [
        "ffmpeg", "-hide_banner", "-loglevel", "warning", "-nostdin", "-y",
        # Reconnect on a dropped source: a torrent-backed URL can stall.
        "-reconnect", "1", "-reconnect_streamed", "1", "-reconnect_delay_max", "5",
        "-i", src,
    ]

    if copy_video:
        maps = ["-map", "0:v:0", "-c:v", "copy", "-map", "0:a:0?", "-c:a", "aac", "-ac", "2", "-b:a", "160k"]
        var_map = "v:0,a:0"
    else:
        # split the decoded video once, scale each branch, encode each branch.
        splits = "".join(f"[v{i}]" for i in range(len(renditions)))
        chains = ";".join(
            f"[v{i}]scale=-2:{r.height}[v{i}out]" for i, r in enumerate(renditions)
        )
        maps = ["-filter_complex", f"[0:v]split={len(renditions)}{splits};{chains}"]

        for i, r in enumerate(renditions):
            maps += [
                "-map", f"[v{i}out]",
                f"-c:v:{i}", encoder,
                f"-b:v:{i}", f"{r.bitrate_k}k",
                f"-maxrate:v:{i}", f"{r.maxrate_k}k",
                f"-bufsize:v:{i}", f"{r.bufsize_k}k",
            ]
            maps += ["-preset", "p4"] if encoder == "h264_nvenc" else ["-preset", "veryfast"]

        for i in range(len(renditions)):
            maps += ["-map", "0:a:0?", f"-c:a:{i}", "aac", "-ac", "2",
                     f"-b:a:{i}", f"{AUDIO_BITRATE_K}k", f"-ar:{i}", "48000"]

        var_map = " ".join(f"v:{i},a:{i}" for i in range(len(renditions)))

    hls = [
        # Force a keyframe exactly on every segment boundary. Expressed in
        # seconds rather than a GOP length in frames, because a frame count only
        # lines up for one frame rate — a 25 or 30 fps source would land its
        # keyframes off the boundaries and switching would break.
        "-force_key_frames", f"expr:gte(t,n_forced*{SEGMENT_SECONDS})",
        "-sc_threshold", "0",
        "-f", "hls",
        "-hls_time", str(SEGMENT_SECONDS),
        # fMP4/CMAF rather than MPEG-TS: the player swaps renditions by feeding
        # a new init segment to Media Source Extensions, which is markedly
        # cleaner than transmuxing TS on the fly, and is what makes a quality
        # switch not stall the video decoder.
        "-hls_segment_type", "fmp4",
        "-hls_fmp4_init_filename", "init.mp4",
        # Every segment is independently decodable, so the player may start at
        # or switch to any of them.
        "-hls_flags", "independent_segments",
        # `event`: segments are retained and the manifest grows, so seeking
        # backward is free. `vod` would require the whole encode to finish first.
        "-hls_playlist_type", "event",
        "-master_pl_name", "master.m3u8",
        "-var_stream_map", var_map,
        "-hls_segment_filename", str(out_dir / "%v" / "seg%d.m4s"),
        str(out_dir / "%v" / "index.m3u8"),
    ]
    return base + maps + hls
