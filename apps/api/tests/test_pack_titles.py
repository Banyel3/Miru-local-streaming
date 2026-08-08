"""Use case: the complete-series packs a sweep will bring back.

Every string here is a real release from the live indexers, found by searching
`one piece batch` / `frieren batch` / `spy x family complete`. They are what a
card needs in order to hold a whole series instead of the eight recent episodes
that happened to be on the indexers' front page.

The parser has to read them or the sweep makes things worse, not better: a pack
whose title parses to something other than the show becomes its own new card,
which is the splitting this catalogue spent a day removing.
"""

from miru.catalog.parse import parse


class TestAPackIsRecognisedAsItsShow:
    def test_the_largest_pack_is_not_filed_as_a_new_show(self):
        # Parsed 'One Piece 0001 1071 Movies' before — a 630 GB pack sitting on
        # a card of its own, next to the real one.
        got = parse(
            "[Anime Time] One Piece (0001-1071+Movies+Specials) [BD+CR] "
            "[1080p][HEVC 10bit x265][AAC][Eng Sub] [Batch]",
            "anime",
        )
        assert got.title == "One Piece"
        assert (got.episode, got.episode_end) == (1, 1071)

    def test_a_zero_padded_range_reads_as_numbers(self):
        got = parse("[Judas] One Piece 001-574 [1080p][HEVC x265 10bit][Batch]", "anime")
        assert got.title == "One Piece"
        assert (got.episode, got.episode_end) == (1, 574)

    def test_a_parenthesised_range_still_works(self):
        got = parse("[SubsPlease] Sousou no Frieren (01-28) (1080p) [Batch]", "anime")
        assert (got.episode, got.episode_end) == (1, 28)

    def test_a_season_pack_says_it_is_complete_without_inventing_numbers(self):
        # No episode numbers anywhere. Guessing a range would be worse than
        # saying "the whole of season 1", which is what the word means.
        got = parse("[Trix] Spy x Family - Season 1 (Part 1 + 2) (COMPLETE) (BDRip 1080p)", "anime")
        assert got.title == "Spy x Family"
        assert got.season == 1
        assert got.complete is True
        assert got.episode is None

    def test_the_word_complete_in_a_scene_name_counts_too(self):
        got = parse("Spy x Family Season 3 Complete 1080p h264", "series")
        assert got.season == 3
        assert got.complete is True

    def test_a_batch_tag_marks_completeness_even_with_a_range(self):
        got = parse("[SubsPlease] Sousou no Frieren (01-28) (1080p) [Batch]", "anime")
        assert got.complete is True


class TestASingleEpisodeIsNotAPack:
    def test_one_episode_is_not_complete(self):
        # The failure that would matter most: marking a single episode complete
        # makes the picker offer it as the whole series.
        got = parse("[SubsPlease] Sousou no Frieren - 09 (1080p) [ABCD1234].mkv", "anime")
        assert got.complete is False
        assert (got.episode, got.episode_end) == (9, None)

    def test_a_small_batch_is_a_range_but_not_complete(self):
        # 741-743 of One Piece is three episodes, not the series.
        got = parse("[RLSP] One Piece 741-743 [BD 720p]", "anime")
        assert (got.episode, got.episode_end) == (741, 743)
        assert got.complete is False

    def test_a_year_is_not_an_episode_range(self):
        got = parse("Frieren Beyond Journeys End S02E07 2023 1080p DSNP WEB-DL", "series")
        assert got.episode == 7
        assert got.episode_end is None
        assert got.complete is False

    def test_a_resolution_is_not_an_episode_range(self):
        got = parse("Some Film 2026 1920x1080 x264", "movie")
        assert got.episode_end is None


class TestAYearInTheTitleIsAYear:
    def test_a_trailing_year_is_lifted_out_of_the_anime_title(self):
        # anitopy leaves it in the title and sets no year, so `One Piece 2023`
        # was a title that fuzzy-matched the 1999 anime at AniList — which is
        # how Netflix's live-action show became the default download for it.
        got = parse("One Piece 2023 S01 COMPLETE 720p NF WEBRip x264", "anime")
        assert got.title == "One Piece"
        assert got.year == 2023

    def test_a_year_that_is_part_of_the_name_is_left_alone(self):
        # Real titles carry numbers. Only a trailing 19xx/20xx is a date.
        got = parse("[SubsPlease] 86 Eighty Six - 09 (1080p)", "anime")
        assert "86" in got.title
