"""Use case: an anime film on the wall.

The metadata provider decides what a show is now, so *Your Name* and
*One Piece Film: Red* resolve at AniList and carry kind=anime. They stop being
Movies-rail entries and land in Anime — which is right, and which would leave a
film sitting between two weekly episode cards with nothing to say it is a film.

AniList already says: `format` is MOVIE for the film and TV for the series. That
is what tells them apart, and it is why the wall does not need a fifth pill —
which would not fit at 375px anyway.
"""

from miru.catalog.models import CatalogWork
from miru.catalog.rails import RAILS, _base


def _w(db, title, fmt, kind="anime"):
    # Complete by default (12 of 12, finished): the strict anime wall hides
    # unknown-count shows, and these tests are about the FORMAT split — they
    # must not start failing for a rule they are not testing. The strict rule
    # has its own class below.
    w = CatalogWork(
        kind=kind, normalised_title=title.casefold(), display_title=title,
        format=fmt, release_count=3, best_seeder_pct=50.0,
        episode_count=12, episodes_covered=12, release_status="FINISHED",
    )
    db.add(w)
    db.commit()
    return w


class TestFilmsHaveTheirOwnRowInsideAnime:
    def test_there_is_a_films_rail_on_the_anime_wall(self):
        from miru.catalog.rails import rails_for

        assert "films" in {r.key for r in rails_for("anime")}

    def test_the_movies_wall_has_no_films_rail(self):
        # It would either duplicate the wall or, filtered the other way, hold
        # everything the wall was already showing.
        from miru.catalog.rails import rails_for

        assert "films" not in {r.key for r in rails_for("movie")}
        assert "films" not in {r.key for r in rails_for("series")}

    def test_the_films_rail_holds_only_films(self, db_session):
        film = _w(db_session, "Your Name", "MOVIE")
        _w(db_session, "Frieren", "TV")
        got = db_session.execute(_base("anime", rail="films")).scalars().all()
        assert [w.id for w in got] == [film.id]

    def test_the_other_rails_do_not_repeat_the_films(self, db_session):
        # Rails are mutually exclusive by design — the same card twice on one
        # screen reads as a bug, and it costs a slot that could show something
        # else.
        _w(db_session, "Your Name", "MOVIE")
        show = _w(db_session, "Frieren", "TV")
        got = db_session.execute(_base("anime", rail="latest")).scalars().all()
        assert [w.id for w in got] == [show.id]

    def test_a_work_with_no_format_yet_is_treated_as_a_series(self, db_session):
        # No FORMAT is not no COUNT: this pins the format routing, so the work
        # is complete and only its format is missing. Guessing "film" would put
        # every such card in a row labelled Films.
        w = _w(db_session, "Not Resolved", None)
        got = db_session.execute(_base("anime", rail="latest")).scalars().all()
        assert [x.id for x in got] == [w.id]

    def test_the_movies_wall_is_unaffected(self, db_session):
        # `format` only splits the anime wall. A live-action film is already in
        # its own kind and must not be filtered out of it.
        film = _w(db_session, "Monay", "MOVIE", kind="movie")
        got = db_session.execute(_base("movie", rail="latest")).scalars().all()
        assert [w.id for w in got] == [film.id]


class TestAnimeSeriesAndAnimeMoviesAreTheirOwnWalls:
    """The user asked for the split as top-level filters, not a rail.

    The provider decides what a show is (kind) and what shape it is (format),
    so the walls are derivable: anime-movies is kind=anime + format=MOVIE,
    anime-series is the rest of anime, and Movies stays live-action because
    anime films carry kind=anime since the identity fix.
    """

    def test_the_anime_movies_wall_holds_only_anime_films(self, db_session):
        film = _w(db_session, "Your Name", "MOVIE")
        _w(db_session, "Frieren", "TV")
        _w(db_session, "Monay", "MOVIE", kind="movie")
        got = db_session.execute(_base("anime-movies", rail="latest")).scalars().all()
        assert [w.id for w in got] == [film.id]

    def test_the_anime_series_wall_excludes_the_films(self, db_session):
        _w(db_session, "Your Name", "MOVIE")
        show = _w(db_session, "Frieren", "TV")
        got = db_session.execute(_base("anime-series", rail="latest")).scalars().all()
        assert [w.id for w in got] == [show.id]

    def test_a_formatless_but_complete_anime_lands_with_the_series(self, db_session):
        # Format routing only: complete but no format goes to anime-series,
        # never to Films. (A COUNTLESS work is hidden by the strict rule — that
        # case is pinned in TestTheAnimeWallShowsOnlyCompleteCards.)
        w = _w(db_session, "Unresolved", None)
        got = db_session.execute(_base("anime-series", rail="latest")).scalars().all()
        assert [x.id for x in got] == [w.id]

    def test_the_api_accepts_the_new_kinds(self, client, db_session):
        for kind in ("anime-series", "anime-movies"):
            assert client.get(f"/api/catalog?kind={kind}").status_code == 200

    def test_the_split_walls_have_no_films_rail(self, db_session):
        # The rail existed to separate films inside the mixed anime wall; on
        # the split walls it would duplicate the pill.
        from miru.catalog.rails import rails_for

        assert "films" not in {r.key for r in rails_for("anime-series")}
        assert "films" not in {r.key for r in rails_for("anime-movies")}


def _anime(db, title, *, covered=0, count=None, aired=None, status=None, fmt="TV"):
    w = CatalogWork(
        kind="anime", normalised_title=title.casefold(), display_title=title,
        format=fmt, release_count=3, best_seeder_pct=50.0,
        episodes_covered=covered, episode_count=count, episodes_aired=aired,
        release_status=status,
    )
    db.add(w)
    db.commit()
    return w


class TestTheAnimeWallShowsOnlyCompleteCards:
    """The user's call, second ask: strict. A finished show is on the wall only
    with every episode of its run; an airing one only with everything aired so
    far; an unknown-count show not at all. Films are complete by nature. Search
    still returns everything — the wall is the only thing being strict.
    """

    def test_a_complete_finished_show_is_on_the_wall(self, db_session):
        w = _anime(db_session, "Naruto", covered=500, count=500, status="FINISHED")
        got = db_session.execute(_base("anime-series", rail="latest")).scalars().all()
        assert [x.id for x in got] == [w.id]

    def test_a_fragment_is_not(self, db_session):
        _anime(db_session, "Daemons", covered=6, count=24, status="FINISHED")
        assert db_session.execute(_base("anime-series", rail="latest")).scalars().all() == []

    def test_an_airing_show_holding_everything_aired_counts_as_complete(self, db_session):
        w = _anime(db_session, "Weekly", covered=6, count=None, aired=6, status="RELEASING")
        got = db_session.execute(_base("anime-series", rail="latest")).scalars().all()
        assert [x.id for x in got] == [w.id]

    def test_an_airing_show_missing_aired_episodes_is_a_fragment(self, db_session):
        _anime(db_session, "Behind", covered=1, aired=18, status="RELEASING")
        assert db_session.execute(_base("anime-series", rail="latest")).scalars().all() == []

    def test_an_unknown_count_show_is_off_the_wall(self, db_session):
        # 175 of 303 today. The user chose strict knowing the wall thins; the
        # background sweep and the half-hourly enrichment are the way back on.
        _anime(db_session, "Unresolved", covered=9)
        assert db_session.execute(_base("anime-series", rail="latest")).scalars().all() == []

    def test_a_merged_season_card_is_complete_by_the_providers_count(self, db_session):
        # Frieren: S2 merged onto the S1 card — covered 38, provider count 28.
        # The provider's denominator is the test, deliberately lenient, so a
        # flagship complete card is not hidden over a season the provider
        # record does not describe.
        w = _anime(db_session, "Frieren", covered=38, count=28, status="FINISHED")
        got = db_session.execute(_base("anime-series", rail="latest")).scalars().all()
        assert [x.id for x in got] == [w.id]

    def test_an_anime_film_needs_no_episode_arithmetic(self, db_session):
        w = _anime(db_session, "Your Name", fmt="MOVIE")
        got = db_session.execute(_base("anime-movies", rail="latest")).scalars().all()
        assert [x.id for x in got] == [w.id]

    def test_anime_rows_inside_the_all_wall_obey_the_same_rule(self, db_session):
        _anime(db_session, "Fragment", covered=2, count=24, status="FINISHED")
        film = _w(db_session, "Monay", "MOVIE", kind="movie")
        got = db_session.execute(_base("all", rail="latest")).scalars().all()
        assert [x.id for x in got] == [film.id]

    def test_the_movies_wall_is_the_one_exemption(self, db_session):
        # Series joined the strict rule once TVmaze/TMDB supplied a
        # denominator (TestTheSeriesWallIsStrictNow). Movies are the exemption
        # that remains: a film with a release is whole.
        m = CatalogWork(kind="movie", normalised_title="film", display_title="Film",
                        release_count=1, best_seeder_pct=1.0)
        db_session.add(m)
        db_session.commit()
        got = db_session.execute(_base("movie", rail="latest")).scalars().all()
        assert [x.id for x in got] == [m.id]


class TestTheSeriesWallIsStrictNow:
    """Live-action was exempt only for lack of a denominator; TVmaze's aired
    list and TMDB's tv detail now supply one, so Kamen-Rider-style fragments
    leave this wall the same way anime fragments left theirs.
    """

    def _series(self, db, title, **kw):
        w = CatalogWork(kind="series", normalised_title=title.casefold(),
                        display_title=title, release_count=3, best_seeder_pct=9.0, **kw)
        db.add(w)
        db.commit()
        return w

    def test_a_complete_ended_series_shows(self, db_session):
        w = self._series(db_session, "Done", episode_count=26, episodes_covered=26,
                         release_status="FINISHED")
        got = db_session.execute(_base("series", rail="latest")).scalars().all()
        assert [x.id for x in got] == [w.id]

    def test_a_fragment_hides(self, db_session):
        self._series(db_session, "Scattered", episode_count=45, episodes_covered=3,
                     release_status="FINISHED")
        assert db_session.execute(_base("series", rail="latest")).scalars().all() == []

    def test_an_unknown_count_series_hides_too(self, db_session):
        self._series(db_session, "Uncounted")
        assert db_session.execute(_base("series", rail="latest")).scalars().all() == []

    def test_series_rows_in_the_all_wall_obey_the_rule(self, db_session):
        self._series(db_session, "Scattered", episode_count=45, episodes_covered=3,
                     release_status="FINISHED")
        film = _w(db_session, "Monay", "MOVIE", kind="movie")
        got = db_session.execute(_base("all", rail="latest")).scalars().all()
        assert [x.id for x in got] == [film.id]

    def test_movies_remain_exempt(self, db_session):
        m = CatalogWork(kind="movie", normalised_title="film", display_title="Film",
                        release_count=1, best_seeder_pct=1.0)
        db_session.add(m)
        db_session.commit()
        got = db_session.execute(_base("movie", rail="latest")).scalars().all()
        assert [x.id for x in got] == [m.id]

    def test_the_completion_sweep_now_hunts_series_as_well(self, db_session):
        from miru.catalog.sweep import completion_candidates

        self._series(db_session, "Scattered", episode_count=45, episodes_covered=3,
                     release_status="FINISHED")
        got = completion_candidates(db_session, limit=8)
        assert [w.display_title for w in got] == ["Scattered"]
