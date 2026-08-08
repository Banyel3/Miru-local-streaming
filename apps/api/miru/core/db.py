from collections.abc import Iterator

from sqlalchemy import create_engine
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
    # ponytail: create_all, not Alembic. One machine, no data that can't be
    # rebuilt by rescanning. Alembic lands with `progress` (M3), which is the
    # first table the filesystem can't regenerate. See ARCHITECTURE.md §3.
    import miru.catalog.models  # noqa: F401  (registers tables on Base)
    import miru.library.models  # noqa: F401

    Base.metadata.create_all(engine)
