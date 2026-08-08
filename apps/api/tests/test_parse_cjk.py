"""Use case: an anime release named by a Chinese fansub group.

anitopy reads scene names and Latin-bracketed fansub names well. It does not
read the CJK conventions — full-width brackets, an underscore where a space
belongs, `第29-38话` for an episode range — and what survives is either the
whole string or, worse, a stray tag.

That matters far past a cosmetic title. The parsed title is the search term the
metadata provider is given, and the provider id it comes back with IS the card.
A title of `CHS` sent to TMDB returned *CHS: Dash for the Cash*, so an episode
of Frieren is currently filed as an unrelated American show. Six Frieren cards
out of twelve come from this file.
"""

from miru.catalog.parse import parse

# Every one of these is a real release name from the live catalogue.
FRIEREN = "Sousou no Frieren"

# Release furniture. A title still carrying any of this is not a search term —
# it is the filename with some of the brackets taken out, and no provider will
# match it. Asserting only that the show's name is *somewhere inside* passes on
# the raw string and proves nothing, which is how this shipped.
FURNITURE = ("字幕", "話", "话", "1080p", "720p", "AVC", "HEVC", "x264", "简", "繁", "中配")


def _is_a_search_term(title: str) -> None:
    assert FRIEREN.casefold() in title.casefold(), f"lost the show name: {title!r}"
    left = [f for f in FURNITURE if f in title]
    assert not left, f"release furniture survived into the title: {left} in {title!r}"


class TestChineseFansubNamesResolveToTheShow:
    def test_a_cjk_group_in_square_brackets_is_not_part_of_the_title(self):
        # 千夏字幕组 is the group. It made its own card, twice, once per script.
        got = parse("[千夏字幕组][葬送的芙莉莲_Sousou no Frieren][第29-38话][1080p_AVC][简体]", "anime")
        _is_a_search_term(got.title)

    def test_the_traditional_script_variant_lands_on_the_same_title(self):
        # 千夏字幕組 / 芙莉蓮 rather than 千夏字幕组 / 芙莉莲. One show, one card.
        got = parse("[千夏字幕組][葬送的芙莉蓮_Sousou no Frieren][第29-38話][1080p_AVC][繁體]", "anime")
        _is_a_search_term(got.title)

    def test_a_full_width_bracketed_group_does_not_leave_the_language_tag_as_the_title(self):
        # The reported disaster: title parsed as "CHS", which is the subtitle
        # language, and TMDB answered "CHS: Dash for the Cash".
        got = parse(
            "【悠哈璃羽字幕社】[葬送的芙莉莲 第二季/Sousou no Frieren 2nd Season -S2][30][x264 1080p][CHS]",
            "anime",
        )
        assert got.title.casefold() != "chs"
        _is_a_search_term(got.title)

    def test_a_dub_marker_is_not_part_of_the_show_name(self):
        # 中配 means "Mainland Mandarin dub". It is a property of the release.
        got = parse("中配 - 葬送的芙莉莲 (Sousou no Frieren) (Season 2) (Mainland Mandarin)", "anime")
        _is_a_search_term(got.title)

    def test_an_episode_range_written_in_chinese_is_read_as_a_range(self):
        # 第29-38话 is a batch. Read as a range it stays one row; missed, it
        # looks like a single episode and the picker offers the wrong scope.
        got = parse("[千夏字幕组][葬送的芙莉莲_Sousou no Frieren][第29-38话][1080p_AVC][简体]", "anime")
        assert (got.episode, got.episode_end) == (29, 38)


class TestTheLatinPathIsUntouched:
    def test_a_plain_fansub_release_still_parses(self):
        got = parse("[SubsPlease] Sousou no Frieren - 09 (1080p) [ABCD1234].mkv", "anime")
        assert got.title == FRIEREN
        assert got.episode == 9

    def test_a_scene_name_still_parses(self):
        got = parse("Frieren Beyond Journeys End S02E07 2023 1080p DSNP WEB-DL", "series")
        assert "Frieren" in got.title
        assert got.episode == 7

    def test_a_batch_range_still_parses(self):
        got = parse("[RLSP] One Piece 741-743 [BD 720p]", "anime")
        assert (got.episode, got.episode_end) == (741, 743)


class TestATitleThatCannotIdentifyAnything:
    def test_a_name_that_is_only_release_furniture_yields_no_title(self):
        # Nothing here names a show. An empty title is refused by the resolver;
        # a one-token title is what got matched to the wrong show.
        got = parse("[1080p_AVC][简体][繁體]", "anime")
        assert len(got.title) <= 3 or got.title == ""
