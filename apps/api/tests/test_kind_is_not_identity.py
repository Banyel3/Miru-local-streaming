"""Use case: the same show arrives from two indexers that disagree about it.

`classify()` reads the indexer's Torznab categories, and the indexers do not
agree. Measured on the live catalogue, for one show:

    series   The Pirate Bay   88 releases    (tags anime as 5000 TV)
    anime    Nyaa.si          43             (tags 5070 TV/Anime)
    anime    Knaben           36
    series   Knaben           18

Neither reading is wrong — The Pirate Bay does not carry an anime tag at all,
so 5000 TV is the only honest thing to make of what it sent. The damage is what
`kind` then does: it picks the metadata provider (anime asks AniList, series
asks TVmaze) and it is part of both unique keys on the works table. So one show
resolved to anilist/154587 and tvmaze/69956 and could never merge.

Frieren is currently twelve cards. This file is about the two largest.
"""

import pytest
from sqlalchemy import select

from miru.catalog import enrich
from miru.catalog.models import CatalogWork

FRIEREN = {
    "provider": "anilist",
    "provider_id": "154587",
    "display_title": "Frieren: Beyond Journey's End",
    "names": ["Sousou no Frieren", "Frieren: Beyond Journey's End"],
    "format": "TV",
    "year": 2023,
}
YOUR_NAME = {
    "provider": "anilist",
    "provider_id": "97962",
    "display_title": "Your Name",
    "names": ["Kimi no Na wa", "Your Name"],
    "format": "MOVIE",
    "year": 2016,
}
BIG_BROTHER = {
    "provider": "tvmaze",
    "provider_id": "7",
    "display_title": "Big Brother",
    "names": ["Big Brother"],
    "format": "TV",
}


def _work(db, kind, title):
    # normalised(), not casefold(): the grouping key strips punctuation, so
    # "Frieren: Beyond Journey's End" keys as "frieren beyond journey's end".
    from miru.catalog.parse import normalised

    w = CatalogWork(kind=kind, normalised_title=normalised(title), display_title=title)
    db.add(w)
    db.commit()
    return w


class TestTheProviderDecidesWhatAShowIs:
    def test_one_show_indexed_as_anime_and_as_series_becomes_one_card(
        self, db_session, monkeypatch
    ):
        """The reported bug, at its root.

        141 Frieren releases sat on two cards in two rails because two indexers
        described the same show differently. The indexer's category is a hint
        about where to look, never a fact about what the show is.
        """
        monkeypatch.setattr(enrich, "lookup", lambda kind, title, year: FRIEREN)
        a = _work(db_session, "anime", "Sousou no Frieren")
        b = _work(db_session, "series", "Frieren Beyond Journeys End")

        enrich.enrich_work(db_session, a)
        db_session.commit()
        enrich.enrich_work(db_session, b)
        db_session.commit()

        works = db_session.execute(select(CatalogWork)).scalars().all()
        assert len(works) == 1, [w.display_title for w in works]

    def test_an_anilist_match_makes_it_anime_whatever_the_indexer_said(
        self, db_session, monkeypatch
    ):
        # AniList holds anime and nothing else, so a match there is a claim
        # about the show rather than about the indexer that carried it.
        monkeypatch.setattr(enrich, "lookup", lambda kind, title, year: FRIEREN)
        w = _work(db_session, "series", "Frieren Beyond Journeys End")
        enrich.enrich_work(db_session, w)
        db_session.commit()
        assert w.kind == "anime"

    def test_an_anime_film_stays_in_anime_and_is_marked_a_film(
        self, db_session, monkeypatch
    ):
        # Your Name is indexed under Movies by every non-anime tracker. It
        # belongs in the Anime rail, told from the weekly shows by `format` —
        # which is why the wall needs no fifth pill.
        monkeypatch.setattr(enrich, "lookup", lambda kind, title, year: YOUR_NAME)
        w = _work(db_session, "movie", "Kimi no Na wa")
        enrich.enrich_work(db_session, w)
        db_session.commit()
        assert (w.kind, w.format) == ("anime", "MOVIE")

    def test_a_genuine_live_action_series_is_left_where_it_was(
        self, db_session, monkeypatch
    ):
        # The guard on all of the above. TVmaze answering must not drag a
        # reality show into the Anime rail.
        monkeypatch.setattr(enrich, "lookup", lambda kind, title, year: BIG_BROTHER)
        w = _work(db_session, "series", "Big Brother")
        enrich.enrich_work(db_session, w)
        db_session.commit()
        assert w.kind == "series"


class TestTheTitleCacheIsAskedOncePerTitle:
    def test_the_same_title_under_two_kinds_is_one_lookup(self, db_session, monkeypatch):
        # The cache was keyed on (kind, title), so the identical question was
        # asked twice and — worse — could be answered differently each time,
        # which is what produced the two provider ids in the first place.
        calls = []

        def lookup(kind, title, year):
            calls.append((kind, title))
            return FRIEREN

        monkeypatch.setattr(enrich, "lookup", lookup)
        from miru.catalog.resolve import resolve

        resolve(db_session, "anime", "Sousou no Frieren", None)
        resolve(db_session, "series", "Sousou no Frieren", None)
        assert len(calls) == 1, calls


class TestATitleIsAskedAboutOnce:
    def test_the_schema_forbids_caching_one_title_under_two_kinds(self, db_session):
        """The live database held 15 of these before the migration.

        Two rows for one title, written because the cache was keyed on
        (kind, query) — and free to disagree, which is exactly how one show
        ended up holding two provider ids that could never merge. The key is
        now the query alone, so the disagreement cannot be recorded.
        """
        import pytest as _pytest
        from sqlalchemy.exc import IntegrityError

        from miru.catalog.models import TitleResolution

        db_session.add(TitleResolution(kind="anime", query="sousou no frieren",
                                       provider="anilist", provider_id="154587",
                                       data=FRIEREN))
        db_session.commit()
        db_session.add(TitleResolution(kind="series", query="sousou no frieren",
                                       provider=None, provider_id=None, data={}))
        with _pytest.raises(IntegrityError):
            db_session.commit()
        db_session.rollback()

    def test_a_resolved_row_is_preferred_if_a_database_still_holds_both(self, db_session):
        # The reader stays tolerant of the old shape, because an API that has
        # been restarted before its migration ran would otherwise take the whole
        # enrichment pass down with MultipleResultsFound.
        from miru.catalog.models import TitleResolution
        from miru.catalog.resolve import _row

        db_session.add(TitleResolution(kind="series", query="q", provider=None,
                                       provider_id=None, data={}))
        db_session.commit()
        got = _row(db_session, "q")
        assert got is not None and got.provider is None


class TestTmdbIdsAreNamespaced:
    def test_a_film_and_a_show_sharing_a_tmdb_id_stay_apart(self, db_session, monkeypatch):
        """TMDB numbers films and television separately.

        Movie 550 and TV 550 are unrelated. Once a provider id is matched
        without the kind beside it, an unnamespaced id would merge them into one
        card offering the wrong download.
        """
        from miru.catalog.resolve import work_by_provider

        film = _work(db_session, "movie", "A Film")
        film.provider, film.provider_id = "tmdb", "movie:550"
        show = _work(db_session, "series", "A Show")
        show.provider, show.provider_id = "tmdb", "tv:550"
        db_session.commit()

        found = work_by_provider(db_session, "movie", {"provider": "tmdb", "provider_id": "movie:550"})
        assert found is not None and found.id == film.id

    def test_the_namespace_is_written_by_the_fetcher(self, monkeypatch):
        monkeypatch.setattr(enrich.settings, "tmdb_api_key", "k")
        monkeypatch.setattr(
            enrich, "_get",
            lambda *a, **kw: {"results": [{"id": 550, "title": "Fight Club", "release_date": "1999-01-01"}]},
        )
        assert enrich._tmdb("Fight Club", None, "movie")["provider_id"] == "movie:550"
        assert enrich._tmdb("Fight Club", None, "series")["provider_id"] == "tv:550"


class TestIngestGroupsByTheKindTheProviderGives:
    def test_a_release_whose_kind_the_provider_changes_finds_the_existing_card(
        self, db_session
    ):
        """Found while re-grouping the live catalogue: an outright crash.

        `_work_for` looked a work up by the indexer's kind and then applied the
        resolution, which now rewrites the kind. So the row it found and the
        identity it ended up with disagreed, and the rename collided with a work
        already holding that identity — `duplicate key value violates unique
        constraint "uq_work_identity"`, taking the pass down.
        """
        from miru.catalog.ingest import _work_for
        from miru.catalog.models import TitleResolution

        # Already on the wall under the provider's own title, in the Anime
        # rail, but never itself resolved — the exact state a card is in after
        # ingest names it and before enrichment reaches it.
        existing = _work(db_session, "anime", "Frieren: Beyond Journey's End")
        existing.year = 2023
        db_session.add(TitleResolution(kind="anime", query="sousou no frieren",
                                       provider="anilist", provider_id="154587",
                                       data=FRIEREN))
        db_session.commit()

        # The same show arriving from an indexer that calls it a series. No work
        # holds the provider id yet, so the provider-id path cannot catch this;
        # the title lookup has to be done under the kind the provider gives,
        # not the one the indexer sent.
        got = _work_for(db_session, "series", "Sousou no Frieren", None)
        db_session.commit()
        assert got.id == existing.id

    def test_an_unresolved_title_still_groups_by_the_indexers_kind(self, db_session):
        # The fallback has to keep working: a title no provider knows groups by
        # kind and title exactly as before, which is what stops a film and a
        # show of the same name becoming one card.
        from miru.catalog.ingest import _work_for

        a = _work_for(db_session, "movie", "Climax", 2018)
        b = _work_for(db_session, "series", "Climax", 2018)
        db_session.commit()
        assert a.id != b.id


class TestAnilistIsAskedFirstWhateverTheIndexerSaid:
    def test_a_show_the_indexer_called_a_series_is_offered_to_anilist(self, monkeypatch):
        """The other half of the same bug.

        Even once the kind stops being identity, a work the indexer called a
        series is only ever offered to TVmaze — which answers, correctly, that
        it is a television show. So the anime and the series halves each keep
        resolving to their own provider and never meet. AniList holds anime and
        nothing else, so asking it first costs a miss on live-action and buys
        the merge on everything else.
        """
        asked = []
        monkeypatch.setattr(enrich, "_anilist", lambda t: asked.append(("anilist", t)))
        monkeypatch.setattr(enrich, "_tvmaze", lambda t: asked.append(("tvmaze", t)))
        monkeypatch.setattr(enrich, "_tmdb", lambda t, y, k: asked.append(("tmdb", t)))
        enrich.lookup("series", "Frieren Beyond Journeys End", None)
        assert asked[0][0] == "anilist", asked

    def test_a_film_is_offered_to_anilist_before_tmdb(self, monkeypatch):
        asked = []
        monkeypatch.setattr(enrich, "_anilist", lambda t: asked.append("anilist"))
        monkeypatch.setattr(enrich, "_tmdb", lambda t, y, k: asked.append("tmdb"))
        enrich.lookup("movie", "Kimi no Na wa", None)
        assert asked[0] == "anilist", asked

    def test_a_live_action_series_still_ends_up_at_tvmaze(self, monkeypatch):
        # The cost of asking first is that AniList must be allowed to say no,
        # and the next source must still be tried.
        monkeypatch.setattr(enrich, "_anilist", lambda t: None)
        monkeypatch.setattr(
            enrich, "_tvmaze",
            lambda t: {"provider": "tvmaze", "provider_id": "7", "names": ["Big Brother"]},
        )
        assert enrich.lookup("series", "Big Brother", None)["provider"] == "tvmaze"

    def test_anime_still_asks_anilist_only_once(self, monkeypatch):
        # It is first for every kind now; it must not also be second for anime.
        asked = []
        monkeypatch.setattr(enrich, "_anilist", lambda t: asked.append(t))
        monkeypatch.setattr(enrich, "_tmdb", lambda t, y, k: None)
        enrich.lookup("anime", "Nothing At All", None)
        assert len(asked) == len(set(asked)), asked


class TestAStatedYearCanOverruleATitleMatch:
    """`One Piece 2023` is Netflix's live-action show, not the 1999 anime.

    Both parse to the title `One Piece`, which resolves to AniList 21 — so the
    live-action episodes merged onto the anime card. It showed: with the picker
    preferring the smallest complete release, the default download for ONE PIECE
    became `One Piece 2023 S01 COMPLETE`, a 2.7 GB live-action season on a card
    for a 1000-episode anime.
    """

    def test_a_release_from_a_different_year_does_not_join_the_card(self, db_session):
        from miru.catalog.ingest import _work_for
        from miru.catalog.models import TitleResolution

        db_session.add(TitleResolution(
            kind="anime", query="one piece", provider="anilist", provider_id="21",
            data={"provider": "anilist", "provider_id": "21",
                  "display_title": "ONE PIECE", "year": 1999,
                  "names": ["One Piece", "ONE PIECE"]},
        ))
        db_session.commit()

        anime = _work_for(db_session, "anime", "One Piece", None)
        db_session.commit()
        live_action = _work_for(db_session, "anime", "One Piece", 2023)
        db_session.commit()
        assert live_action.id != anime.id

    def test_the_same_year_still_merges(self, db_session):
        from miru.catalog.ingest import _work_for
        from miru.catalog.models import TitleResolution

        db_session.add(TitleResolution(
            kind="anime", query="frieren", provider="anilist", provider_id="154587",
            data={"provider": "anilist", "provider_id": "154587",
                  "display_title": "Frieren", "year": 2023, "names": ["Frieren"]},
        ))
        db_session.commit()
        a = _work_for(db_session, "anime", "Frieren", 2023)
        db_session.commit()
        b = _work_for(db_session, "anime", "Frieren", None)
        db_session.commit()
        assert a.id == b.id

    def test_one_year_out_is_not_a_different_show(self, db_session):
        # A season that aired across New Year is dated either way by different
        # groups. Splitting on that would undo the merging this catalogue does.
        from miru.catalog.ingest import _work_for
        from miru.catalog.models import TitleResolution

        db_session.add(TitleResolution(
            kind="anime", query="bleach", provider="anilist", provider_id="269",
            data={"provider": "anilist", "provider_id": "269",
                  "display_title": "Bleach", "year": 2004, "names": ["Bleach"]},
        ))
        db_session.commit()
        a = _work_for(db_session, "anime", "Bleach", 2004)
        db_session.commit()
        b = _work_for(db_session, "anime", "Bleach", 2005)
        db_session.commit()
        assert a.id == b.id
