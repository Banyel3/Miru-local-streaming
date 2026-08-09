"""the shape of the run, for the complete-only anime wall

The home screen showed anime cards holding scattered episodes. The user chose a
strict wall: anime rails show only complete cards - every episode of the known
run, which is episode_count for finished shows and aired-so-far
(nextAiringEpisode - 1) for airing ones. These columns carry the provider's
denominator and our own covered-union numerator.

Revision ID: b3e8f92c4a17
Revises: a9c4e71f3b25
"""

import sqlalchemy as sa
from alembic import op

revision = "b3e8f92c4a17"
down_revision = "a9c4e71f3b25"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("catalog_works", sa.Column("release_status", sa.String(16), nullable=True))
    op.add_column("catalog_works", sa.Column("episodes_aired", sa.Integer(), nullable=True))
    op.add_column(
        "catalog_works",
        sa.Column("episodes_covered", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("catalog_works", "episodes_covered")
    op.drop_column("catalog_works", "episodes_aired")
    op.drop_column("catalog_works", "release_status")
