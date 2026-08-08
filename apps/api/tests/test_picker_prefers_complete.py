"""Use case: pressing Download on a series card.

The card's default should be the thing that makes the card complete, because
that is what the user opened it for. It ranked on quality, PC-avoidance,
preferred group and seeders, and knew nothing about whether a release was one
episode or the whole run — so on a card holding a 1-915 pack it would still
offer episode 1172.

Sizes here are the real ones from the live indexers.
"""

import pytest

from miru.catalog.rank import Candidate, pick_default, three_choices

GB = 1 << 30


def _c(cid, size, *, quality="1080p", seeders=100, complete=False, ep=None, end=None):
    return Candidate(
        id=cid, title=cid, indexer="Nyaa.si", seeders=seeders, size_bytes=size,
        quality=quality, group=None, grabbable=True, stale=False,
        episode=ep, episode_end=end, complete=complete,
    )


class TestTheDefaultIsTheSmallestCompleteThing:
    def test_a_complete_pack_beats_a_single_episode(self):
        single = _c("single", 1 * GB, ep=1172)
        pack = _c("pack", 42 * GB, complete=True, ep=1, end=28)
        assert pick_default([single, pack]).id == "pack"

    def test_the_smallest_complete_pack_wins(self):
        # 630 GB against 860 GB free is 73% of the disk in one click. A season
        # is complete too, and it is the answer the card was opened for.
        season = _c("season", 42 * GB, complete=True, ep=1, end=28)
        whole = _c("whole", 630 * GB, complete=True, ep=1, end=1071)
        assert pick_default([season, whole]).id == "season"

    def test_completeness_outranks_quality(self):
        # Deliberate, and the reverse of how quality normally ranks: a complete
        # 720p run is a series you can watch and a 1080p episode is not.
        pack = _c("pack", 20 * GB, quality="720p", complete=True, ep=1, end=24)
        one = _c("one", 1 * GB, quality="1080p", ep=12)
        assert pick_default([pack, one]).id == "pack"

    def test_a_dead_pack_does_not_win(self):
        # Seeders still gate everything. A pack nobody is seeding is not a way
        # to complete a series, it is a download that never finishes.
        dead = _c("dead", 42 * GB, seeders=0, complete=True, ep=1, end=28)
        alive = _c("alive", 1 * GB, seeders=200, ep=1172)
        assert pick_default([dead, alive]).id == "alive"

    def test_with_no_pack_at_all_nothing_changes(self):
        # Most shows have no pack, and the old ranking is right for them.
        a = _c("a", 1 * GB, quality="1080p", ep=1)
        b = _c("b", 1 * GB, quality="480p", ep=2)
        assert pick_default([a, b]).id == "a"


class TestTheOtherTwoChoicesStillAnswerTheirOwnQuestion:
    def test_smallest_is_still_the_smallest(self):
        # Not the smallest complete one. The user asked for the smallest.
        single = _c("single", 1 * GB, ep=1172)
        pack = _c("pack", 42 * GB, complete=True, ep=1, end=28)
        assert three_choices([single, pack])["smallest"].id == "single"

    def test_best_quality_is_still_the_best_quality(self):
        pack = _c("pack", 42 * GB, quality="720p", complete=True, ep=1, end=28)
        sharp = _c("sharp", 2 * GB, quality="2160p", ep=1172)
        assert three_choices([pack, sharp])["best_quality"].id == "sharp"

    def test_best_is_the_complete_one(self):
        single = _c("single", 1 * GB, ep=1172)
        pack = _c("pack", 42 * GB, complete=True, ep=1, end=28)
        assert three_choices([single, pack])["best"].id == "pack"


class TestACompleteUnitStartsAtTheBeginning:
    def test_a_mid_series_batch_does_not_beat_one_that_starts_at_episode_one(self):
        """Found live: the ONE PIECE default became `Episodes 838-875`.

        Uploaders tag any multi-episode release a batch, so "complete" on its
        own picked the smallest self-declared batch — which can be a chunk out
        of the middle. A card opened to be completed should start at episode 1.
        """
        middle = _c("middle", 5 * GB, complete=True, ep=838, end=875)
        start = _c("start", 150 * GB, complete=True, ep=1, end=915)
        assert pick_default([middle, start]).id == "start"

    def test_among_runs_from_episode_one_the_smallest_still_wins(self):
        small = _c("small", 150 * GB, quality="480p", complete=True, ep=1, end=915)
        huge = _c("huge", 549 * GB, quality="1080p", complete=True, ep=1, end=900)
        assert pick_default([small, huge]).id == "small"

    def test_a_season_pack_with_no_numbers_still_counts(self):
        # `Spy x Family Season 1 COMPLETE` states no episodes at all, and it is
        # exactly the complete unit the card wants.
        season = _c("season", 4 * GB, complete=True)
        single = _c("single", 1 * GB, ep=7)
        assert pick_default([season, single]).id == "season"

    def test_a_mid_series_batch_still_beats_a_single_episode(self):
        # It is more of the show than one episode, just not the start.
        middle = _c("middle", 5 * GB, complete=True, ep=838, end=875)
        single = _c("single", 1 * GB, ep=1172)
        assert pick_default([middle, single]).id == "middle"
