"""when a show was last asked about for complete packs

The catalogue is built from each indexer's front page, which is about a day
deep, so a card holds the encodings of the episodes uploaded this week and
nothing else — ONE PIECE had 206 releases covering 82 distinct episodes of 1172.
The missing ones are only reachable by querying, and this records when we last
did, so opening a card does not fire a search at four indexers on every poll.

Revision ID: e7a3c19d5b82
Revises: d51c8e2b7a04
"""

import sqlalchemy as sa
from alembic import op

revision = "e7a3c19d5b82"
down_revision = "d51c8e2b7a04"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "catalog_works",
        sa.Column("swept_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("catalog_works", "swept_at")
