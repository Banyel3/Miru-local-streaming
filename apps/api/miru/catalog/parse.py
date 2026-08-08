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


# Anything in the CJK blocks: Han, hiragana, katakana, and the full-width
# brackets that come with them. Presence of one of these is what switches the
# pre-clean on, so a Latin release name never goes near it.
_CJK = re.compile(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uff00-\uffef]")

# A Chinese fansub group names itself with one of these. They are the tokens
# that made 千夏字幕组 into a show.
_SUBBER = re.compile(r"[\u4e00-\u9fff]{2,}(?:字幕组|字幕組|字幕社|压制组|漫遊|发布组)")

# 第29-38话 / 第29-38話 / 第30话. anitopy does not read these, so a batch looked
# like a single episode and the picker offered the wrong scope.
_CJK_EPISODE = re.compile(r"第\s*(\d{1,4})\s*[-–~]\s*(\d{1,4})\s*[话話集]|第\s*(\d{1,4})\s*[话話集]")

# Dub, script and subtitle-language markers. Properties of the release, not of
# the show — and `CHS` alone as a title is what TMDB answered with
# "CHS: Dash for the Cash", filing an episode of Frieren as an American show.
_CJK_TAGS = re.compile(
    r"(?<![A-Za-z])(?:CHS|CHT|GB|BIG5|JPSC|JPTC)(?![A-Za-z])|"
    r"中配|简体|簡體|繁体|繁體|简繁|簡繁|内嵌|內嵌|外挂|外掛|合集|國語|国语|粵語|粤语",
    re.I,
)


def _cjk_episode(name: str) -> tuple[int | None, int | None]:
    """The episode or episode range a Chinese release name carries."""
    m = _CJK_EPISODE.search(name)
    if not m:
        return None, None
    if m.group(1):
        return int(m.group(1)), int(m.group(2))
    return int(m.group(3)), None


# A bracket group that is release furniture rather than a name: resolution,
# codec, source, container, a CRC32 hash, a bare episode number.
_FURNITURE_GROUP = re.compile(
    r"^\s*(?:\d{1,4}|[0-9A-F]{8}|v\d|"
    r"(?:\d{3,4}[pi]|x?26[45]|hevc|avc|aac|flac|opus|ma10p|hi10p|8bit|10bit|"
    r"bd(?:rip)?|web(?:-?dl|rip)?|dvd(?:rip)?|tv|mp4|mkv|sub|subs|"
    r"ova|oad|nc(?:op|ed)|repack|reseed|多版本|附|\s|_|-)+)\s*$",
    re.I,
)


def _prefer_the_latin_name(name: str) -> str:
    """Where a release names the show twice, keep the name a provider indexes.

    `葬送的芙莉莲 Sousou no Frieren` is one show written twice, native then
    romaji. Keeping both makes a search term that matches neither: AniList
    holds the *Japanese* native title 葬送のフリーレン, not the Chinese one.

    The CJK run is dropped in place rather than the longest Latin run being
    lifted out, because lifting loses whatever sat on the far side of it:
    `Youjo Senki 幼女戦記 Movie` would become `Youjo Senki`, merging the film
    into the series. A wrongly merged card offers the wrong download, which is
    the one trade this module says it will not make.
    """
    if re.search(r"[A-Za-z][A-Za-z0-9'!:.\-]{3,}", name):
        out = re.sub(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff]+", " ", name)
        return re.sub(r"\s+", " ", out).strip(" -")

    # No romaji anywhere. A run containing kana is the Japanese title, which is
    # what AniList holds as `native` — the Chinese rendering of the same show is
    # indexed nowhere and matches nothing.
    kana = [r for r in re.split(r"\s+", name) if re.search(r"[\u3040-\u30ff]", r)]
    return max(kana, key=len) if kana else name


def _parse_cjk(name: str) -> Parsed:
    """Parse a release named by a Chinese or Japanese fansub group.

    anitopy reads `[Group] Title - 01 [1080p][HASH]` and reads it well. The CJK
    convention is a different shape: the group named in Han characters, an
    underscore where a space belongs, `第29-38话` for an episode range, and the
    show named twice — natively and in romaji — inside one bracket. anitopy
    recognises none of it, and rather than fight it with a cleaned string that
    it then re-misreads, this reads the shape directly.

    It is not cosmetic. The parsed title is the search term handed to the
    metadata provider, and the provider id that comes back IS the card. Six of
    the twelve Frieren cards on the live wall came from failing this: one of
    them parsed to `CHS`, the subtitle-language tag, and TMDB answered
    *CHS: Dash for the Cash*.
    """
    t = name.replace("【", "[").replace("】", "]").replace("（", "(").replace("）", ")")
    t = _SUBBER.sub(" ", t)
    t = _CJK_TAGS.sub(" ", t)
    t = re.sub(r"第\s*[0-9一二三四五六七八九十]+\s*[季期部]", " ", t)
    first, last = _cjk_episode(t)
    t = _CJK_EPISODE.sub(" ", t)
    # An underscore separates the native and romaji names, and so does a slash:
    # `葬送的芙莉莲_Sousou no Frieren` is one show written twice, not one word.
    t = t.replace("_", " ").replace("/", " ")

    # Keep only the parts that could be a name. Splitting on the delimiters
    # rather than stripping them keeps `[1080p]` separable from the title,
    # which is the whole reason the bracket convention exists.
    parts = [p for p in re.split(r"[\[\]()]", t) if p.strip()]
    named = [p.strip() for p in parts if not _FURNITURE_GROUP.match(p)]
    if first is None:
        # A bracket holding nothing but the episode number: `[...][30][1080p]`.
        # It reads as furniture and is dropped from the title, correctly — but
        # it has to be read before it goes.
        bare = [p.strip() for p in parts if re.fullmatch(r"\s*\d{1,4}\s*", p)]
        if bare:
            first = int(bare[0])
    text = _prefer_the_latin_name(" ".join(named).strip(" -"))

    # Season before episode, so `S2` is not read as episode 2. Only ever from a
    # marker that says season — `第29-38话` is an episode range, and reading its
    # 29 as a season is how a batch ended up filed under season 29.
    season = None
    if m := re.search(r"\bseason\s*(\d{1,2})|\b(\d)(?:nd|rd|st|th)\s+season\b|\bS(\d{1,2})(?=\b|E)", text, re.I):
        season = int(m.group(1) or m.group(2) or m.group(3))
    text = re.sub(
        r"\b(?:\d(?:nd|rd|st|th)\s+)?season\s*\d{0,2}|\bS\d{1,2}\b|"
        r"\b(?:mainland\s+)?(?:mandarin|cantonese|japanese|english)\b",
        " ", text, flags=re.I,
    )

    if first is None:
        # `- 09` at the end of the name part, the other common shape.
        if m := re.search(r"[\s\-]\s*(\d{1,4})(?:\s*[-–~]\s*(\d{1,4}))?\s*$", text):
            first = int(m.group(1))
            last = int(m.group(2)) if m.group(2) else None
            text = text[: m.start()]

    title = re.sub(r"\s+", " ", text).strip(" -")

    return Parsed(
        title=_clean(title),
        year=int(y.group(1)) if (y := re.search(r"\b(19\d{2}|20\d{2})\b", name)) else None,
        season=season,
        episode=first,
        episode_end=last if last != first else None,
        # Off the underscore-normalised text: `1080p_AVC` has no word boundary
        # after the p, so the resolution is invisible in the original.
        quality=_resolution_of(t),
        group=None,
    )


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

    # The CJK convention is a different shape and anitopy does not read it.
    if _CJK.search(name):
        return _parse_cjk(name)

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
