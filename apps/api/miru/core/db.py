from collections.abc import Iterator

from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from miru.core.config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    with SessionLocal() as db:
        yield db


def create_all() -> None:
    """Build the schema on a fresh database. Alembic owns every change after.

    create_all can add a table but never alter one, and the catalogue
    accumulates — Prowlarr exposes no pagination, so its 669 releases cannot be
    rebuilt by asking again. Schema changes therefore go through migrations,
    run from apps/api:

        alembic upgrade head

    A database this function actually creates is stamped at head, so the two
    never disagree about what has already been applied.
    """
    import miru.catalog.models  # noqa: F401  (registers tables on Base)
    import miru.library.models  # noqa: F401

    fresh = not inspect(engine).has_table("catalog_works")
    Base.metadata.create_all(engine)
    if fresh:
        _stamp_head()


def _stamp_head() -> None:
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    cfg = Config(str(Path(__file__).resolve().parents[2] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[2] / "alembic"))
    with engine.connect() as conn:
        # Handed the live connection so env.py does not open a second one from a
        # URL that may have been swapped out from under it.
        cfg.attributes["connection"] = conn
        command.stamp(cfg, "head")
        conn.commit()
