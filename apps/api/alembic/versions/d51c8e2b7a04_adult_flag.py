"""the provider's adult flag, so it never reaches a rail

The category filter handles indexers that categorise honestly. It cannot help
with Nyaa, which files adult anime under TV/Anime like everything else — the
category is not even wrong. So an adult title classified clean, ingested,
resolved, and appeared on the home page's Trending rail with 11 releases.

AniList carries `isAdult` and TMDB carries `adult`. Same move as the rest of
this catalogue: the provider decides, not a string.

Defaults to false. Unknown is not adult — assuming otherwise would empty the
wall of everything the providers have not answered about yet.

Revision ID: d51c8e2b7a04
Revises: c3f21a90de41
"""

import sqlalchemy as sa
from alembic import op

revision = "d51c8e2b7a04"
down_revision = "c3f21a90de41"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "catalog_works",
        sa.Column("adult", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("catalog_works", "adult")
