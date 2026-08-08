"""whether a release is the whole of what it names

A season pack carries no episode numbers at all, so completeness cannot be
inferred from the range and has to be recorded when the name is parsed. The
picker needs it: on a card holding a 1-915 One Piece pack it was still offering
episode 1172, because it could not tell one episode from the whole run.

Revision ID: f2b8d47e1a09
Revises: e7a3c19d5b82
"""

import sqlalchemy as sa
from alembic import op

revision = "f2b8d47e1a09"
down_revision = "e7a3c19d5b82"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "catalog_releases",
        sa.Column("complete", sa.Boolean(), nullable=False, server_default="false"),
    )


def downgrade() -> None:
    op.drop_column("catalog_releases", "complete")
