"""Alembic wiring.

Two entry points, both supported: the `alembic` command line, which opens its
own connection from settings, and `create_all()`, which hands in the connection
it just used so a freshly created database can be stamped without guessing at a
second URL.
"""

from alembic import context
from sqlalchemy import create_engine

from miru.core.config import settings
from miru.core.db import Base

import miru.catalog.models  # noqa: F401  (registers tables on Base)
import miru.library.models  # noqa: F401

target_metadata = Base.metadata


def _run(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_offline() -> None:
    context.configure(
        url=settings.database_url, target_metadata=target_metadata, literal_binds=True
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    with create_engine(settings.database_url, pool_pre_ping=True).connect() as conn:
        _run(conn)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
