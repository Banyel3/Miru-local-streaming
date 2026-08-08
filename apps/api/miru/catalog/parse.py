"""Turning release names into works.

The wall shows works, not releases. Measured on the live indexers, the top of
the anime list by seeders is four separate cards for the same show:

    [RLSP] One Piece 744-746 [BD 720p]
    [RLSP] One Piece 721-724 [BD 720p]
    [RLSP] One Piece 729-732 [BD 720p]

A wall built straight from release rows is a wall of One Piece. Grouping needs
the title out of the name, and release naming is a well-trodden problem: anitopy
is the Python port of Anitomy, which every anime client uses for the
`[Group] Title - 01 [1080p][HASH]` shape, and guessit handles scene and p2p
naming. Route by kind, because each is good at the shape it was written for and
poor at the other.

When grouping is uncertain, split. A wrongly split card shows the same show
twice, which is cosmetic; a wrongly merged card offers the wrong download,
which is not.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from miru.catalog.classify import ANIME
from miru.transcode.strategy import DIRECT, REMUX, TRANSCODE_FULL

# Names that carry a codec the browser cannot play. Deliberately narrow: this
# runs on a filename, not a probe, so it only claims the cases the scene names
# unambiguously. Anything unrecognised is assumed playable, matching
# resolve_strategy()'s bias — a wrong "direct" costs one failed play, a wrong
# "transcode" costs GPU time on a file that never needed it.
_GPU_CODEC = re.compile(r"\b(x265|h\.?265|hevc|av1)\b", re.I)
_HI10 = re.compile(r"\b(10.?bit|hi10p?)\b", re.I)
_SAFE_CODEC = re.compile(r"\b(x264|h\.?264|avc)\b", re.I)

# Camcorder rips. Excluded from the default pick outright — nobody sets out to
# download one, and they sort well on seeders because they arrive first.
JUNK = re.compile(r"\b(CAM|HDCAM|CAMRIP|TS|HDTS|TELESYNC|TELECINE|SCR|SCREENER)\b")

_RESOLUTION = re.compile(r"\b(2160p|1080p|720p|576p|480p|360p)\b", re.I)
_DIMENSIONS = re.compile(r"\b(\d{3,4})\s*x\s*(\d{3,4})\b", re.I)


@dataclass
class Parsed:
    title: str
    year: int | None = None
    season: int | None = None
    episode: int | None = None
    episode_end: int | None = None  # batches: "744-746" ends at 746
    quality: str | None = None      # normalised to 1080p / 720p / ...
    group: str | None = None


def _clean(title: str) -> str:
    """Strip the punctuation that makes the same show group as two."""
    t = unicodedata.normalize("NFKC", title or "")
    t = re.sub(r"[‘’“”]", "'", t)
    t = re.sub(r"[^\w\s'&]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def normalised(title: str) -> str:
    """The grouping key. Lowercased, punctuation-free, articles kept.

    Articles are kept on purpose: dropping "The" merges *The Office* with
    *Office*, and the collision costs more than the split it prevents.
    """
    return _clean(title).casefold()


def _resolution_of(name: str) -> str | None:
    if m := _RESOLUTION.search(name):
        return m.group(1).lower()
    # Nyaa releases often carry raw dimensions instead: "[BD 1920x1080 x264]".
    if m := _DIMENSIONS.search(name):
        height = int(m.group(2))
        for cut, label in ((2000, "2160p"), (1000, "1080p"), (700, "720p"), (500, "576p")):
            if height >= cut:
                return label
        return "480p"
    return None


def _parse_anime(name: str) -> Parsed:
    import anitopy

    raw = anitopy.parse(name) or {}
    eps = raw.get("episode_number")
    if isinstance(eps, list):
        nums = [int(e) for e in eps if str(e).isdigit()]
        first, last = (min(nums), max(nums)) if nums else (None, None)
    elif eps is not None and str(eps).isdigit():
        first, last = int(eps), None
    else:
        first, last = None, None

    season = raw.get("anime_season")
    if isinstance(season, list):
        season = season[0] if season else None

    return Parsed(
        title=_clean(raw.get("anime_title") or name),
        year=int(raw["anime_year"]) if str(raw.get("anime_year", "")).isdigit() else None,
        season=int(season) if str(season).isdigit() else None,
        episode=first,
        episode_end=last if last != first else None,
        quality=_resolution_of(name),
        group=raw.get("release_group") or None,
    )


def _parse_general(name: str) -> Parsed:
    from guessit import guessit

    raw = guessit(name)

    def one(key):
        v = raw.get(key)
        return v[0] if isinstance(v, list) and v else (None if isinstance(v, list) else v)

    episode = one("episode")
    episodes = raw.get("episode")
    end = max(episodes) if isinstance(episodes, list) and len(episodes) > 1 else None

    return Parsed(
        title=_clean(str(one("title") or name)),
        year=int(raw["year"]) if isinstance(raw.get("year"), int) else None,
        season=one("season") if isinstance(one("season"), int) else None,
        episode=episode if isinstance(episode, int) else None,
        episode_end=end,
        quality=_resolution_of(name) or (str(one("screen_size")) if one("screen_size") else None),
        group=str(one("release_group")) if one("release_group") else None,
    )


def parse(name: str, kind: str) -> Parsed:
    """Parse a release name, routed by kind.

    Never raises: a name that defeats both parsers still has to appear on the
    wall, so the fallback is the raw name as its own title. That produces a
    one-release card, which is honest, rather than dropping the release.
    """
    try:
        return _parse_anime(name) if kind == ANIME else _parse_general(name)
    except Exception:
        return Parsed(title=_clean(name) or name, quality=_resolution_of(name))


def predict_strategy(name: str) -> str:
    """What playback will *probably* need, guessed from the release name.

    This is the thing no general torrent client can tell you, because none of
    them own the player: whether grabbing this release means the PC has to be
    awake. It is a prediction from a filename, not a probe — the real strategy
    is resolved by resolve_strategy() once the file exists and always wins.

    Returns the same vocabulary as resolve_strategy() so the UI has one set of
    words for the whole ladder.
    """
    if _GPU_CODEC.search(name) or _HI10.search(name):
        return TRANSCODE_FULL
    if _SAFE_CODEC.search(name):
        # H.264 in an MKV still needs the container rewritten, which the laptop
        # does on its own. "Plays without the PC" is the claim being made.
        return REMUX
    return DIRECT


def needs_pc(name: str) -> bool:
    """Whether this release would wake the PC. The claim the picker makes."""
    return predict_strategy(name) == TRANSCODE_FULL
