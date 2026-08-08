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
    w = CatalogWork(
        kind=kind, normalised_title=title.casefold(), display_title=title,
        format=fmt, release_count=3, best_seeder_pct=50.0,
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
        # Unresolved works have no format. Guessing "film" would put every
        # unresolved card in a row labelled Films.
        w = _w(db_session, "Not Resolved", None)
        got = db_session.execute(_base("anime", rail="latest")).scalars().all()
        assert [x.id for x in got] == [w.id]

    def test_the_movies_wall_is_unaffected(self, db_session):
        # `format` only splits the anime wall. A live-action film is already in
        # its own kind and must not be filtered out of it.
        film = _w(db_session, "Monay", "MOVIE", kind="movie")
        got = db_session.execute(_base("movie", rail="latest")).scalars().all()
        assert [w.id for w in got] == [film.id]
