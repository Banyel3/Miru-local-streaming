"""Use case: turning what the indexers return into a browsable wall.

Every shape in here was taken from a real Prowlarr response on 2026-08-08, not
invented. The cases that matter are the ones where the obvious implementation is
wrong, and each of those is called out where it appears.
"""

import pytest

from miru.catalog.classify import classify
from miru.catalog.parse import needs_pc, normalised, parse, predict_strategy
from miru.catalog.rank import (
    Candidate,
    all_viable_dead,
    pick_default,
    seeder_percentiles,
    three_choices,
    usable,
)


class TestClassification:
    def test_nyaa_tags_anime_as_movies_too_and_anime_wins(self):
        # THE case. Every Nyaa release carries both 5070 TV/Anime and
        # 2020 Movies/Other, which is why all 130 of its anime results came back
        # inside a request for Movies. Without anime-first precedence the Movies
        # rail is One Piece batches.
        assert classify([5070, 131088, 2020]) == "anime"

    def test_nyaa_private_category_block_is_still_anime(self):
        # Some rows carry only the indexer's own numbering, with no 5070.
        assert classify([127720]) == "anime"
        assert classify([134634]) == "anime"

    def test_yts_and_tpb_movies_classify_as_movies(self):
        assert classify([2040, 100045]) == "movie"
        assert classify([2000]) == "movie"

    def test_television_classifies_as_series(self):
        assert classify([5030]) == "series"
        assert classify([5000]) == "series"

    def test_categories_without_names_still_classify(self):
        # Measured: category objects arrive as {"id": 131088} with no "name".
        # A name-based rule drops these silently.
        assert classify([131088]) == "anime"

    def test_non_video_is_dropped_rather_than_guessed(self):
        assert classify([3000]) is None      # audio
        assert classify([7000]) is None      # books
        assert classify([]) is None


class TestParsingReleaseNames:
    def test_a_batch_release_yields_the_show_not_the_batch(self):
        # "[RLSP] One Piece 744-746" must group with every other One Piece
        # release, or the wall is a wall of One Piece.
        p = parse("[RLSP] One Piece 744-746 [BD 720p]", "anime")
        assert p.title == "One Piece"
        assert (p.episode, p.episode_end) == (744, 746)
        assert p.quality == "720p"
        assert p.group == "RLSP"

    def test_nyaa_raw_dimensions_become_a_quality_label(self):
        # Nyaa writes "[BD 1920x1080 x264 FLAC]" rather than "1080p".
        p = parse("[Kashikoi] Ore Monogatari!! - NCOP+NCED [BD 1920x1080 x264 FLAC]", "anime")
        assert p.quality == "1080p"
        assert p.group == "Kashikoi"

    def test_a_yts_movie_yields_title_and_year(self):
        p = parse("Governor (2026) 1080p WEBRip 5.1 x264 -YTS", "movie")
        assert (p.title, p.year, p.quality, p.group) == ("Governor", 2026, "1080p", "YTS")

    def test_a_dotted_scene_name_yields_title_and_year(self):
        p = parse("Tuner.2025.720p.BluRay.x264-KNiVES", "movie")
        assert (p.title, p.year, p.quality) == ("Tuner", 2025, "720p")

    def test_a_name_that_defeats_the_parser_still_becomes_a_card(self):
        # Dropping a release because it will not parse loses it from the wall
        # entirely, which is worse than a one-release card.
        p = parse("", "movie")
        assert p.title == "" or isinstance(p.title, str)
        p2 = parse("!!!!!!", "anime")
        assert isinstance(p2.title, str)

    def test_grouping_key_survives_punctuation_and_case(self):
        assert normalised("Ore Monogatari!!") == normalised("ore monogatari")
        assert normalised("Fate/Zero") == normalised("fate zero")

    def test_grouping_key_keeps_articles(self):
        # Dropping "The" merges The Office with Office. The split costs less
        # than the collision.
        assert normalised("The Office") != normalised("Office")


class TestPredictingWhetherThePcIsNeeded:
    """The thing no general torrent client can tell you, because none of them
    own the player."""

    def test_hevc_and_10bit_names_are_predicted_to_need_the_gpu(self):
        for name in (
            "Stargate 1994 Extended Remastered 720p BluRay x265 10Bit-Pahe in",
            "Some.Film.2024.2160p.UHD.BluRay.HEVC-FraMeSToR",
            "[Group] Show - 01 [1080p][Hi10P][AAC]",
        ):
            assert needs_pc(name), name

    def test_h264_names_are_predicted_not_to_need_the_gpu(self):
        for name in (
            "Governor (2026) 1080p WEBRip 5.1 x264 -YTS",
            "Tuner.2025.720p.BluRay.x264-KNiVES",
            "[RLSP] One Piece 744-746 [BD 720p H.264]",
        ):
            assert not needs_pc(name), name

    def test_an_unrecognised_name_is_optimistic(self):
        # Same bias as resolve_strategy: a wrong "direct" costs one failed play,
        # a wrong "transcode" costs GPU time on a file that never needed it.
        assert predict_strategy("Some.Thing.2024") == "direct"


class TestRankingAcrossIndexers:
    """Seeder counts are not comparable between indexers. Measured on one
    snapshot: Nyaa max 303, YTS zero for two thirds of its catalogue, TPB
    ceiling 2. Raw sorting returns Nyaa, all of it, every time."""

    def _mixed(self):
        return [
            Candidate("nyaa-top", "Show 1080p x264-RLSP", "Nyaa.si", 303, 1_400_000_000, "1080p"),
            Candidate("nyaa-mid", "Show 720p x264-Sub", "Nyaa.si", 10, 700_000_000, "720p"),
            Candidate("yts-a", "Film (2026) 1080p x264 -YTS", "YTS", 0, 2_000_000_000, "1080p"),
            Candidate("yts-b", "Film (2026) 720p x264 -YTS", "YTS", 0, 1_100_000_000, "720p"),
            Candidate("tpb", "Other 720p x264-KNiVES", "The Pirate Bay", 2, 7_000_000_000, "720p"),
        ]

    def test_an_indexer_that_reports_nothing_is_neither_promoted_nor_buried(self):
        pct = seeder_percentiles(self._mixed())
        assert pct["yts-a"] == 0.5 and pct["yts-b"] == 0.5

    def test_a_low_count_at_a_low_reporting_indexer_still_ranks_well(self):
        # TPB's 2 seeders is the top of TPB and must not lose to Nyaa's 10
        # purely because Nyaa reports bigger numbers. Both indexers are given a
        # real sample here, because a percentile over two rows is meaningless
        # whichever indexer it came from.
        items = [
            Candidate(f"n{i}", f"Show {i} 1080p x264", "Nyaa.si", n, 1_000_000_000, "1080p")
            for i, n in enumerate([303, 100, 60, 30, 10, 4])
        ] + [
            Candidate(f"t{i}", f"Other {i} 720p x264", "The Pirate Bay", n, 7_000_000_000, "720p")
            for i, n in enumerate([2, 2, 1, 1, 1, 0])
        ]
        pct = seeder_percentiles(items)
        assert pct["t0"] == 1.0                 # top of its own indexer
        assert pct["t0"] > pct["n4"]            # beats Nyaa's 10-seeder row

    def test_a_sample_too_small_to_rank_is_not_ranked(self):
        # A percentile over one or two results is an accident, not a ranking:
        # a lone 1-seeder result would score 1.0 and top the Trending rail.
        # The regional catalog_queries produce exactly that shape.
        pct = seeder_percentiles(self._mixed())
        assert pct["tpb"] == 0.5
        assert pct["nyaa-top"] == 0.5

    def test_a_real_sample_still_ranks(self):
        items = [
            Candidate(f"n{i}", f"Show {i} 1080p x264", "Nyaa.si", n, 1_000_000_000, "1080p")
            for i, n in enumerate([300, 200, 100, 50, 10, 1])
        ]
        pct = seeder_percentiles(items)
        assert pct["n0"] == 1.0
        assert pct["n5"] < pct["n0"]


class TestPickingOnTheUsersBehalf:
    def test_a_cam_rip_is_never_the_default_however_well_seeded(self):
        # Camcorder rips arrive first and seed hardest. Nobody sets out to
        # download one.
        items = [
            Candidate("cam", "Film 2026 HDCAM x264", "YTS", 900, 700_000_000, "480p"),
            Candidate("good", "Film 2026 1080p BluRay x264", "YTS", 40, 2_000_000_000, "1080p"),
        ]
        assert pick_default(items).id == "good"
        assert [c.id for c in usable(items)] == ["good"]

    def test_quality_outranks_sparing_the_pc(self):
        # Regression. Ranking on needs_pc first picked a 480p rip over a 1080p
        # one, which spares a GPU that exists precisely to be used. Avoiding the
        # PC breaks a tie inside a quality tier; it does not drop two tiers.
        items = [
            Candidate("sd", "Show S01E16 480p x264", "TPB", 8, 240_000_000, "480p"),
            Candidate("hd", "Show S01E16 1080p x265", "TPB", 14, 630_000_000, "1080p"),
        ]
        assert pick_default(items).id == "hd"

    def test_a_release_that_avoids_waking_the_pc_wins_a_tie(self):
        # The differentiator. Same quality, x264 beats x265 because the PC can
        # stay asleep.
        items = [
            Candidate("hevc", "Film 1080p BluRay x265-Pahe", "YTS", 100, 900_000_000, "1080p"),
            Candidate("avc", "Film 1080p BluRay x264-RLSP", "YTS", 60, 2_000_000_000, "1080p"),
        ]
        assert pick_default(items).id == "avc"

    def test_a_dead_torrent_is_never_the_default(self):
        items = [
            Candidate("dead", "Film 1080p x264", "YTS", 0, 2_000_000_000, "1080p"),
            Candidate("alive", "Film 720p x264", "YTS", 30, 1_100_000_000, "720p"),
        ]
        assert pick_default(items).id == "alive"

    def test_ungrabbable_and_stale_releases_are_excluded(self):
        items = [
            Candidate("no-link", "Film 1080p x264", "YTS", 500, 2e9, "1080p", grabbable=False),
            Candidate("old", "Film 1080p x264", "YTS", 500, 2e9, "1080p", stale=True),
            Candidate("ok", "Film 720p x264", "YTS", 20, 1e9, "720p"),
        ]
        assert pick_default(items).id == "ok"

    def test_nothing_grabbable_returns_nothing_rather_than_a_corpse(self):
        items = [Candidate("x", "Film CAM", "YTS", 0, 1e9, "480p", grabbable=False)]
        assert pick_default(items) is None
        assert three_choices(items) == {"best": None, "smallest": None, "best_quality": None}

    def test_the_sheet_can_tell_the_user_the_swarm_is_dead(self):
        thin = [Candidate("a", "Film 1080p x264", "YTS", 1, 2e9, "1080p")]
        assert all_viable_dead(thin)
        healthy = [Candidate("a", "Film 1080p x264", "YTS", 40, 2e9, "1080p")]
        assert not all_viable_dead(healthy)


class TestTheThreeChoices:
    def _set(self):
        return [
            Candidate("best", "Film 1080p BluRay x264-RLSP", "Nyaa.si", 1470, 1_400_000_000, "1080p"),
            Candidate("small", "Film 720p BluRay x265-Pahe", "Nyaa.si", 340, 610_000_000, "720p"),
            Candidate("uhd", "Film 2160p UHD x265-FraMeSToR", "Nyaa.si", 88, 12_400_000_000, "2160p"),
        ]

    def test_the_three_named_choices_are_distinct_and_correct(self):
        c = three_choices(self._set())
        assert c["best"].id == "best"
        assert c["smallest"].id == "small"
        assert c["best_quality"].id == "uhd"

    def test_best_quality_is_honest_when_there_is_no_4k(self):
        # Offering a "Best quality" row that is the same 1080p as "Best" is
        # honest; inventing a 4K row that does not exist is not.
        items = [
            Candidate("a", "Film 1080p x264", "YTS", 40, 2e9, "1080p"),
            Candidate("b", "Film 720p x264", "YTS", 90, 1e9, "720p"),
        ]
        assert three_choices(items)["best_quality"].id == "a"

    @pytest.mark.parametrize("name", ["best", "smallest", "best_quality"])
    def test_every_named_choice_is_something_the_user_can_actually_grab(self, name):
        choice = three_choices(self._set())[name]
        assert choice is not None and choice.grabbable and choice.seeders > 0
